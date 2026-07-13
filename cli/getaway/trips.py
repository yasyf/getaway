import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import resources

import click

from getaway import prefs
from getaway.constants import CABIN_PREFIX, PHASE_TTL_HOURS
from getaway.paths import (
    NegativePredicate,
    StateConflictError,
    UsageError,
    atomic_update,
    atomic_write_text,
    current_pointer,
    emit,
    locked,
    map_errors,
    require_int,
    require_int_or_none,
    require_keys,
    require_str,
    require_str_list,
    require_str_or_none,
    trip_dir,
    trips_dir,
    utcnow,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
ARTIFACT_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.(json|jsonl)$")
BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED_BUCKET_NAMES = frozenset({"gateways", "onward"})
RESERVED_KEYS = frozenset({"slug", "created"})
PLAN_KEYS = frozenset(
    {
        "origins",
        "buckets",
        "program_sweeps",
        "hybrid",
        "sources",
        "mileage_ceiling",
        "max_finalists",
        "round_trip",
    }
)
HYBRID_KEYS = frozenset({"gateways", "onward_dests", "max_hybrids"})
JUDGMENT_KEYS = frozenset({"guidance", "factors"})
FACTOR_PRIORITIES = frozenset({"primary", "secondary", "note"})
TRIP_FP_KEYS = (
    "window",
    "cabin",
    "party",
    "regions",
    "vibe",
    "avoid_final_destinations",
    "plan",
    "judgment",
)
PREFS_FP_KEYS = (
    "origin_airports",
    "avoid_transit",
    "avoid_destinations",
    "avoid_airlines",
    "layovers",
    "documents",
    "departure_days",
)
PREFS_RANK_KEYS = ("balances", "statuses", "credits", "status_goals")
RANK_PHASES = frozenset({"rank", "finalize"})

# Placeholders resolved per trip: direct-shortlist sweep artifacts, active-collector evidence.
_SWEEP_DEPS = "@sweeps"
_EVIDENCE_DEPS = "@evidence"
# Deps that only exist on the hybrid gateway/onward/bridge path.
_HYBRID_ONLY_ARTIFACTS = frozenset(
    {"sweep-gateways.jsonl", "shortlist-gateway.json", "onward.json", "bridge.json"}
)

# Source of truth for each phase's upstream deps. A dep that should exist but is absent hashes as
# a sentinel, so its later arrival flips the fingerprint. Keys absent here (sweeps) run on inputs.
PHASE_ARTIFACT_DEPS: dict[str, list[str]] = {
    "shortlist": [_SWEEP_DEPS],
    "shortlist:gateway": ["sweep-gateways.jsonl"],
    "onward": ["shortlist-gateway.json"],
    "bridge": ["onward.json"],
    "expand": ["shortlist.json", "shortlist-gateway.json"],
    "evidence.verify": ["expand.json"],
    "evidence.cash": ["expand.json"],
    "evidence.context": ["shortlist.json"],
    "evidence.transit": ["expand.json", "shortlist-gateway.json"],
    "assess": ["expand.json", _EVIDENCE_DEPS],
    "rank": ["shortlist.json", "expand.json", "assess.json"],
    "finalize": ["rank.json", "onward.json", "bridge.json"],
}
_ABSENT = b"\x00ABSENT\x00"


def _template() -> dict:
    return {
        "slug": None,
        "created": None,
        "status": "planning",
        "ask": None,
        "window": {"start": None, "end": None, "trip_length_days": None},
        "cabin": None,
        "party": 1,
        "regions": {"include": [], "exclude": []},
        "vibe": [],
        "avoid_final_destinations": [],
        "plan": {},
        "judgment": {},
        "decisions": [],
    }


TEMPLATE_KEYS = frozenset(_template())
SETTABLE_KEYS = TEMPLATE_KEYS - RESERVED_KEYS


def _valid_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise UsageError(f"invalid trip slug: {slug!r}")
    return slug


def _trip_json(slug: str):
    return trip_dir(slug) / "trip.json"


def _checkpoints_path(slug: str):
    return trip_dir(slug) / "checkpoints.json"


def _artifact_path(slug: str, name: str):
    _valid_slug(slug)
    if not ARTIFACT_RE.match(name):
        raise UsageError(f"invalid artifact name: {name!r}")
    return trip_dir(slug) / "artifacts" / name


def _factor_ids() -> set[str]:
    data = json.loads((resources.files("getaway") / "data" / "factors.json").read_text())
    return {f["id"] for f in data["factors"]}


def _validate_bucket(bucket: object) -> None:
    bucket = require_keys(bucket, {"name", "dests"}, "plan.buckets row")
    name = require_str(bucket["name"], "plan.buckets.name")
    if not BUCKET_NAME_RE.match(name):
        raise UsageError(f"plan.buckets.name must match {BUCKET_NAME_RE.pattern!r}: {name!r}")
    if name in RESERVED_BUCKET_NAMES:
        raise UsageError(f"plan.buckets.name is a reserved label: {name!r}")
    require_str_list(bucket["dests"], "plan.buckets.dests")


def _validate_plan(plan: object, merged: dict) -> None:
    plan = require_keys(plan, set(), "plan", optional=frozenset(PLAN_KEYS))
    if "round_trip" in plan and not isinstance(plan["round_trip"], bool):
        raise UsageError("plan.round_trip must be a boolean")
    if "buckets" in plan:
        if not isinstance(plan["buckets"], list):
            raise UsageError("plan.buckets must be a list")
        for bucket in plan["buckets"]:
            _validate_bucket(bucket)
    if "hybrid" in plan:
        hybrid = require_keys(plan["hybrid"], set(), "plan.hybrid", optional=frozenset(HYBRID_KEYS))
        if "onward_dests" in hybrid:
            require_str_list(hybrid["onward_dests"], "plan.hybrid.onward_dests")
            vetoed = set(merged["avoid_final_destinations"])
            vetoed |= set(prefs.show()["avoid_destinations"])
            bad = sorted({a for a in hybrid["onward_dests"] if a in vetoed})
            if bad:
                raise UsageError(f"plan.hybrid.onward_dests vetoed by avoid lists: {bad}")


def _validate_judgment(judgment: object) -> None:
    judgment = require_keys(judgment, set(), "judgment", optional=frozenset(JUDGMENT_KEYS))
    if "guidance" in judgment:
        require_str(judgment["guidance"], "judgment.guidance")
    if "factors" in judgment:
        factors = judgment["factors"]
        if not isinstance(factors, dict):
            raise UsageError("judgment.factors must be an object")
        valid = _factor_ids()
        for fid, spec in factors.items():
            if fid not in valid:
                raise UsageError(f"unknown judgment factor id: {fid!r}")
            spec = require_keys(spec, {"priority"}, f"judgment.factors[{fid}]")
            if spec["priority"] not in FACTOR_PRIORITIES:
                raise UsageError(
                    f"judgment.factors[{fid}].priority must be one of {sorted(FACTOR_PRIORITIES)}"
                )


def _validate_trip(merged: dict) -> None:
    require_str(merged["status"], "status")
    require_str_or_none(merged["ask"], "ask")
    window = require_keys(merged["window"], {"start", "end", "trip_length_days"}, "window")
    require_str_or_none(window["start"], "window.start")
    require_str_or_none(window["end"], "window.end")
    require_int_or_none(window["trip_length_days"], "window.trip_length_days")
    if merged["cabin"] is not None and merged["cabin"] not in CABIN_PREFIX:
        raise UsageError(f"cabin must be one of {sorted(CABIN_PREFIX)} or null")
    if require_int(merged["party"], "party") < 1:
        raise UsageError("party must be >= 1")
    regions = require_keys(merged["regions"], {"include", "exclude"}, "regions")
    require_str_list(regions["include"], "regions.include")
    require_str_list(regions["exclude"], "regions.exclude")
    require_str_list(merged["vibe"], "vibe")
    require_str_list(merged["avoid_final_destinations"], "avoid_final_destinations")
    _validate_plan(merged["plan"], merged)
    _validate_judgment(merged["judgment"])
    if not isinstance(merged["decisions"], list):
        raise UsageError("decisions must be a list")


def new(slug: str, ask: str | None = None, now: Callable[[], datetime] = utcnow) -> dict:
    _valid_slug(slug)
    stamped = _template()
    stamped["slug"] = slug
    stamped["created"] = now().isoformat()
    stamped["ask"] = ask

    def _mut(current: dict) -> dict:
        if current:
            raise StateConflictError(f"trip {slug!r} already exists")
        return stamped

    doc = atomic_update(_trip_json(slug), _mut)
    current_set(slug)
    return doc


def set_patch(slug: str, patch: dict) -> dict:
    _valid_slug(slug)
    reserved = set(patch) & RESERVED_KEYS
    if reserved:
        raise UsageError(f"reserved keys cannot be set: {sorted(reserved)}")
    unknown = set(patch) - SETTABLE_KEYS
    if unknown:
        raise UsageError(f"unknown trip keys: {sorted(unknown)}")

    def _mut(current: dict) -> dict:
        if not current:
            raise StateConflictError(f"no trip {slug!r}")
        merged = {**current, **patch}
        _validate_trip(merged)
        return merged

    return atomic_update(_trip_json(slug), _mut)


def show(slug: str) -> dict:
    _valid_slug(slug)
    path = _trip_json(slug)
    if not path.exists():
        raise StateConflictError(f"no trip {slug!r}")
    return json.loads(path.read_text())


def list_() -> list[str]:
    root = trips_dir()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "trip.json").exists())


def done(slug: str) -> dict:
    _valid_slug(slug)

    def _mut(current: dict) -> dict:
        if not current:
            raise StateConflictError(f"no trip {slug!r}")
        return {**current, "status": "done"}

    doc = atomic_update(_trip_json(slug), _mut)
    pointer = current_pointer()
    with locked(pointer):
        if pointer.exists() and pointer.read_text() == slug:
            pointer.unlink()
    return doc


def current_get() -> str | None:
    pointer = current_pointer()
    return pointer.read_text() if pointer.exists() else None


def current_set(slug: str) -> None:
    _valid_slug(slug)
    atomic_write_text(current_pointer(), slug)


def log(slug: str, text: str, now: Callable[[], datetime] = utcnow) -> dict:
    _valid_slug(slug)
    entry = {"ts": now().isoformat(), "text": text}

    def _mut(current: dict) -> dict:
        if not current:
            raise StateConflictError(f"no trip {slug!r}")
        current["decisions"].append(entry)
        return current

    atomic_update(_trip_json(slug), _mut)
    return entry


def _sha(obj: object) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _phase_base(key: str) -> str:
    return key.split(":", 1)[0]


def capture_inputs_fp(trip: dict, prefs_doc: dict, key: str) -> str:
    """Fingerprint the trip+prefs inputs of a phase, captured at work start.

    The CLI-internal stampers (sweeps.run, shortlist, factors.rank, factors.finalize) call this
    before their long-running work and hand the result to ``phase_done(inputs_fp=...)``, so a
    concurrent trip/prefs edit mid-work marks the phase stale rather than stamping the new
    fingerprint over rows derived from the old inputs.
    """
    payload = {
        "trip": {k: trip[k] for k in TRIP_FP_KEYS},
        "prefs": {k: prefs_doc[k] for k in PREFS_FP_KEYS},
    }
    if _phase_base(key) in RANK_PHASES:
        payload["prefs_rank"] = {k: prefs_doc[k] for k in PREFS_RANK_KEYS}
    return _sha(payload)


def _direct_sweep_artifacts(trip: dict, prefs_doc: dict) -> list[str]:
    from getaway.sweeps import derive_specs

    return [
        f"sweep-{spec['label']}.jsonl"
        for spec in derive_specs(trip, prefs_doc)
        if spec["label"] != "gateways"
    ]


def _evidence_artifacts(trip: dict, prefs_doc: dict, slug: str) -> list[str]:
    from getaway.constants import EVIDENCE_COLLECTORS

    profile = _judgment_profile(trip, prefs_doc, slug)
    return [
        f"evidence-{EVIDENCE_COLLECTORS[fid]}.json"
        for fid, spec in profile.items()
        if spec["active"] and fid in EVIDENCE_COLLECTORS
    ]


def _resolve_deps(slug: str, key: str, trip: dict, prefs_doc: dict) -> list[str]:
    template = PHASE_ARTIFACT_DEPS.get(key)
    if template is None:
        return []
    hybrid = bool(trip["plan"].get("hybrid"))
    deps: list[str] = []
    for item in template:
        if item == _SWEEP_DEPS:
            deps.extend(_direct_sweep_artifacts(trip, prefs_doc))
        elif item == _EVIDENCE_DEPS:
            deps.extend(_evidence_artifacts(trip, prefs_doc, slug))
        elif item in _HYBRID_ONLY_ARTIFACTS and not hybrid:
            continue
        else:
            deps.append(item)
    return deps


def _upstream_fp(slug: str, key: str, trip: dict, prefs_doc: dict) -> str | None:
    deps = _resolve_deps(slug, key, trip, prefs_doc)
    if not deps:
        return None
    digest = hashlib.sha256()
    for name in deps:
        path = _artifact_path(slug, name)
        digest.update(name.encode())
        digest.update(b"\x00")
        digest.update(path.read_bytes() if path.exists() else _ABSENT)
        digest.update(b"\x00")
    return digest.hexdigest()


def _ttl_ok(record: dict, key: str, now: Callable[[], datetime]) -> bool:
    ttl = PHASE_TTL_HOURS.get(_phase_base(key))
    if ttl is None:
        return True
    completed = datetime.fromisoformat(record["completed_at"])
    return now() - completed <= timedelta(hours=ttl)


def _load_checkpoints(slug: str) -> dict:
    path = _checkpoints_path(slug)
    return json.loads(path.read_text()) if path.exists() else {}


def phase_check(
    slug: str, key: str, now: Callable[[], datetime] = utcnow
) -> tuple[bool, dict | None]:
    _valid_slug(slug)
    record = _load_checkpoints(slug).get(key)
    if record is None:
        return False, None
    trip = show(slug)
    prefs_doc = prefs.show()
    fresh = (
        record["inputs_fp"] == capture_inputs_fp(trip, prefs_doc, key)
        and record["upstream_fp"] == _upstream_fp(slug, key, trip, prefs_doc)
        and _ttl_ok(record, key, now)
    )
    return fresh, record


def phase_fresh(slug: str, key: str, now: Callable[[], datetime] = utcnow) -> bool:
    return phase_check(slug, key, now=now)[0]


def phase_done(
    slug: str,
    key: str,
    artifacts: list[str] | None = None,
    quota_after: int | None = None,
    now: Callable[[], datetime] = utcnow,
    inputs_fp: str | None = None,
) -> dict:
    # ``artifacts`` is accepted for the CLI --artifact flag and factors.py's positional call, but
    # upstream deps now come from PHASE_ARTIFACT_DEPS, so it no longer feeds the fingerprint.
    _valid_slug(slug)
    trip = show(slug)
    prefs_doc = prefs.show()
    if inputs_fp is None:
        inputs_fp = capture_inputs_fp(trip, prefs_doc, key)
    record = {
        "completed_at": now().isoformat(),
        "inputs_fp": inputs_fp,
        "upstream_fp": _upstream_fp(slug, key, trip, prefs_doc),
    }
    if quota_after is not None:
        record["quota_after"] = quota_after
    atomic_update(_checkpoints_path(slug), lambda d: {**d, key: record})
    return record


def artifact_write(slug: str, name: str, content: str) -> None:
    path = _artifact_path(slug, name)
    try:
        if name.endswith(".json"):
            json.loads(content)
        else:
            for line in content.splitlines():
                if line.strip():
                    json.loads(line)
    except json.JSONDecodeError as err:
        raise UsageError(f"artifact {name!r} failed to parse: {err}") from err
    atomic_write_text(path, content)


def artifact_read(slug: str, name: str) -> str:
    return _artifact_path(slug, name).read_text()


def artifact_list(slug: str) -> list[str]:
    _valid_slug(slug)
    directory = trip_dir(slug) / "artifacts"
    if not directory.exists():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file() and ARTIFACT_RE.match(p.name))


def existing_artifacts(slug: str, names: list[str]) -> list[str]:
    present = set(artifact_list(slug))
    return [name for name in names if name in present]


def _optional_artifact(slug: str, name: str) -> dict | None:
    path = _artifact_path(slug, name)
    return json.loads(path.read_text()) if path.exists() else None


def _sweep_labels(trip: dict, prefs_doc: dict) -> list[str]:
    from getaway.sweeps import derive_specs

    labels = [spec["label"] for spec in derive_specs(trip, prefs_doc)]
    if trip["plan"].get("hybrid"):
        labels.append("onward")
    return labels


def _judgment_profile(trip: dict, prefs_doc: dict, slug: str) -> dict:
    from getaway import factors, registry

    profile_doc = factors.derive_profile(trip, prefs_doc, slug=slug)
    kinds = {f["id"]: f["kind"] for f in registry.factors()}
    return {fid: spec for fid, spec in profile_doc.items() if "judgment" in kinds[fid]}


def _phase_keys(trip: dict, prefs_doc: dict, active_judgment: list[str]) -> list[str]:
    from getaway.constants import EVIDENCE_COLLECTORS

    plan = trip["plan"]
    keys = [f"sweep:{label}" for label in _sweep_labels(trip, prefs_doc)]
    keys.append("shortlist")
    if plan.get("hybrid"):
        keys += ["shortlist:gateway", "onward", "bridge"]
    for fid in active_judgment:
        collector = EVIDENCE_COLLECTORS.get(fid)
        if collector is not None:
            keys.append(f"evidence.{collector}")
    keys += ["expand", "assess", "rank", "finalize"]
    return keys


def _latest_quota(now: Callable[[], datetime]) -> dict | None:
    from getaway.paths import cache_db
    from getaway.store import NoData, connect

    store = connect(cache_db(), now=now)
    try:
        return store.latest_quota()
    except NoData:
        return None


def status(slug: str, now: Callable[[], datetime] = utcnow) -> dict:
    trip = show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    judgment_profile = _judgment_profile(trip, prefs_doc, slug)
    active_judgment = [fid for fid, spec in judgment_profile.items() if spec["active"]]
    keys = _phase_keys(trip, prefs_doc, active_judgment)
    phase_map = {key: "fresh" if phase_fresh(slug, key, now=now) else "stale" for key in keys}
    return {
        "slug": slug,
        "sweep_labels": _sweep_labels(trip, prefs_doc),
        "hybrid": plan.get("hybrid"),
        "round_trip": plan.get("round_trip", False),
        "active_factors": active_judgment,
        "max_finalists": plan.get("max_finalists", 6),
        "party": trip["party"],
        "phase_map": phase_map,
        "quota": _latest_quota(now),
    }


def profile(slug: str) -> dict:
    from getaway import factors

    trip = show(slug)
    return factors.derive_profile(trip, prefs.show(), slug=slug)


def resume(slug: str, now: Callable[[], datetime] = utcnow) -> str:
    from getaway import learnings

    trip = show(slug)
    st = status(slug, now=now)
    window = trip["window"]
    lines = [f"Trip {slug} — status: {trip['status']}"]
    if trip["ask"]:
        lines.append(f"Ask: {trip['ask']}")
    lines.append(
        f"Window: {window['start']} to {window['end']} "
        f"({window['trip_length_days']}d), cabin {trip['cabin']}, party {trip['party']}"
    )
    if trip["vibe"]:
        lines.append(f"Vibe: {', '.join(trip['vibe'])}")
    decisions = trip["decisions"][-5:]
    if decisions:
        lines.append("Recent decisions:")
        lines += [f"  - {d['ts']}: {d['text']}" for d in decisions]
    lines.append("Phase freshness:")
    lines += [f"  {key}: {state}" for key, state in st["phase_map"].items()]
    finalists = _optional_artifact(slug, "finalists.json")
    if finalists is not None:
        directs = finalists["directs"]
        hybrids = finalists["hybrids"]
        lines.append(f"Finalists: {len(directs)} direct, {len(hybrids)} hybrid")
        for entry in directs[:5]:
            c = entry["candidate"]
            lines.append(f"  {c['origin']}-{c['dest']} {c['date']} {c['source']} {c['mileage']} mi")
    expiring = prefs.credit_list("90d", now=now)
    if expiring:
        lines.append("Credits expiring within 90d:")
        lines += [
            f"  {c['issuer']} {c['amount']} {c['currency']} — expires {c['expires']}"
            for c in expiring
        ]
    api = learnings.list_(scope="api", n=5)
    if api:
        lines.append("Recent api learnings:")
        lines += [f"  - {entry['text']}" for entry in api]
    return "\n".join(lines)


trip_group = click.Group("trip", help="Per-trip planning memory and artifacts.")


@trip_group.command("new")
@click.argument("slug")
@click.option("--ask", default=None)
@map_errors
def _new_cmd(slug: str, ask: str | None) -> None:
    emit(new(slug, ask))


@trip_group.command("set")
@click.argument("slug")
@map_errors
def _set_cmd(slug: str) -> None:
    try:
        patch = json.loads(click.get_text_stream("stdin").read())
    except json.JSONDecodeError as err:
        raise UsageError(f"invalid JSON on stdin: {err}") from err
    emit(set_patch(slug, patch))


@trip_group.command("show")
@click.argument("slug")
@map_errors
def _show_cmd(slug: str) -> None:
    emit(show(slug))


@trip_group.command("list")
@map_errors
def _list_cmd() -> None:
    emit(list_())


@trip_group.command("done")
@click.argument("slug")
@map_errors
def _done_cmd(slug: str) -> None:
    emit(done(slug))


@trip_group.command("current")
@click.argument("slug", required=False)
@map_errors
def _current_cmd(slug: str | None) -> None:
    if slug is None:
        emit({"current": current_get()})
    else:
        current_set(slug)
        emit({"current": slug})


@trip_group.command("log")
@click.argument("slug")
@click.argument("text")
@map_errors
def _log_cmd(slug: str, text: str) -> None:
    emit(log(slug, text))


@trip_group.command("phase-check")
@click.argument("slug")
@click.argument("key")
@map_errors
def _phase_check_cmd(slug: str, key: str) -> None:
    fresh, record = phase_check(slug, key)
    emit({"fresh": fresh, "record": record})
    if not fresh:
        raise NegativePredicate("phase stale")


@trip_group.command("phase-done")
@click.argument("slug")
@click.argument("key")
@click.option("--artifact", "artifacts", multiple=True)
@click.option("--quota-after", type=int, default=None)
@map_errors
def _phase_done_cmd(
    slug: str, key: str, artifacts: tuple[str, ...], quota_after: int | None
) -> None:
    emit(phase_done(slug, key, list(artifacts), quota_after))


@trip_group.command("status")
@click.argument("slug")
@map_errors
def _status_cmd(slug: str) -> None:
    emit(status(slug))


@trip_group.command("profile")
@click.argument("slug")
@map_errors
def _profile_cmd(slug: str) -> None:
    emit(profile(slug))


@trip_group.command("resume")
@click.argument("slug")
@map_errors
def _resume_cmd(slug: str) -> None:
    click.echo(resume(slug))


@trip_group.command("finalize")
@click.argument("slug")
@map_errors
def _finalize_cmd(slug: str) -> None:
    from getaway import factors

    emit(factors.finalize(slug))


@trip_group.group("artifact")
def _artifact_group() -> None:
    """Read and write per-trip artifact files."""


@_artifact_group.command("write")
@click.argument("slug")
@click.argument("name")
@map_errors
def _artifact_write_cmd(slug: str, name: str) -> None:
    content = click.get_text_stream("stdin").read()
    artifact_write(slug, name, content)
    emit({"slug": slug, "name": name, "bytes": len(content)})


@_artifact_group.command("read")
@click.argument("slug")
@click.argument("name")
@map_errors
def _artifact_read_cmd(slug: str, name: str) -> None:
    click.echo(artifact_read(slug, name), nl=False)


@_artifact_group.command("list")
@click.argument("slug")
@map_errors
def _artifact_list_cmd(slug: str) -> None:
    emit(artifact_list(slug))
