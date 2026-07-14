import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import resources

import click

from getaway import prefs, registry
from getaway.constants import (
    CABIN_PREFIX,
    DISJOINT_DURABLE_PREF_KEYS,
    NODE_QUOTA_COST,
    NODE_ROUTING,
    NODE_TTL_HOURS,
    NOTABLE_PREFERENCE_STRETCH_LIMIT,
)
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
ARTIFACT_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ARTIFACT_LEAF_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.(json|jsonl)$")
BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED_BUCKET_NAMES = frozenset({"gateways", "onward"})
RESERVED_KEYS = frozenset({"slug", "created"})
TRIP_TYPES = frozenset({"one_way", "round_trip"})
PLAN_KEYS = frozenset(
    {
        "trip_type",
        "origins",
        "buckets",
        "program_sweeps",
        "hybrid",
        "sources",
        "preferences",
        "constraints",
        "return",
        "lodging",
    }
)
HYBRID_KEYS = frozenset({"gateways", "onward_dests", "max_hybrids"})
RETURN_KEYS = frozenset({"origins", "dests"})
LODGING_KEYS = frozenset({"checkout"})
JUDGMENT_KEYS = frozenset({"guidance", "factors"})
FACTOR_PRIORITIES = frozenset({"primary", "secondary", "note"})
ASSESS_VERDICTS = frozenset({"promote", "neutral", "demote"})
BRIDGE_QUOTE_KEYS = frozenset(
    {
        "gateway",
        "onward_dest",
        "date",
        "cabin",
        "source",
        "price",
        "currency",
        "duration_minutes",
        "stops",
        "connections",
        "airline",
        "flight_number",
        "departs_local",
        "arrives_local",
    }
)
JUDGMENT_FACTOR_KINDS = frozenset({"judgment", "deterministic+judgment"})
DAY_TOKENS = frozenset({"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})
PREFERENCE_KEYS = frozenset(
    {
        "outbound_departure_window",
        "return_arrival_by",
        "trip_length",
        "departure_days",
        "cabin",
        "mileage_target",
    }
)
CONSTRAINT_KEYS = frozenset(
    {
        "outbound_departure_window",
        "return_arrival_by",
        "departure_days",
        "cabin",
        "mileage_limit",
    }
)
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
    "home_airport",
    "origin_airports",
    "avoid_transit",
    "avoid_destinations",
    "avoid_airlines",
    "layovers",
    "documents",
    "departure_days",
)
PREFS_RANK_KEYS = ("balances", "statuses", "travel_instruments", "status_goals")
RANK_PHASES = frozenset({"rank", "finalize"})
# An absent declared input hashes distinctly, so its later arrival flips the fingerprint.
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
    *dirs, leaf = name.split("/")
    if not ARTIFACT_LEAF_RE.match(leaf) or not all(ARTIFACT_SEGMENT_RE.match(d) for d in dirs):
        raise UsageError(f"invalid artifact name: {name!r}")
    return trip_dir(slug).joinpath("artifacts", *name.split("/"))


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


def _iso_date(value: object, label: str) -> None:
    text = require_str(value, label)
    try:
        datetime.fromisoformat(text)
    except ValueError as err:
        raise UsageError(f"{label} is not an ISO date: {value!r}") from err


def _str_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise UsageError(f"{label} must be a list of strings")
    result = [x for x in value if isinstance(x, str)]
    if len(result) != len(value):
        raise UsageError(f"{label} must be a list of strings")
    return result


def _vetoed_dests(merged: dict) -> set[str]:
    return set(merged["avoid_final_destinations"]) | set(prefs.show()["avoid_destinations"])


def _reject_disjoint(key: object, where: str) -> None:
    if key in DISJOINT_DURABLE_PREF_KEYS:
        raise UsageError(f"{where}[{key!r}] is a durable-preferences key, not a trip-doc key")


def _require_confirmed(spec: dict, label: str) -> None:
    if spec["confirmed"] is not True:
        raise UsageError(f"{label}.confirmed must be true — a constraint is explicitly confirmed")


def _validate_pref_value(key: object, value: object, label: str) -> None:
    if key == "outbound_departure_window":
        win = require_keys(value, {"start", "end"}, label)
        _iso_date(win["start"], f"{label}.start")
        _iso_date(win["end"], f"{label}.end")
    elif key == "return_arrival_by":
        win = require_keys(value, {"latest_local_date"}, label)
        _iso_date(win["latest_local_date"], f"{label}.latest_local_date")
    elif key == "trip_length":
        spec = require_keys(value, {"days", "basis"}, label)
        require_int(spec["days"], f"{label}.days")
        require_str(spec["basis"], f"{label}.basis")
    elif key == "departure_days":
        bad = sorted({d for d in _str_list(value, label) if d not in DAY_TOKENS})
        if bad:
            raise UsageError(f"{label} has invalid day tokens: {bad}")
    elif key == "cabin":
        if value not in CABIN_PREFIX:
            raise UsageError(f"{label} must be one of {sorted(CABIN_PREFIX)}")
    elif key == "mileage_target":
        spec = require_keys(value, {"miles", "scope"}, label)
        require_int(spec["miles"], f"{label}.miles")
        require_str(spec["scope"], f"{label}.scope")


def _validate_constraint_value(key: object, value: object, label: str) -> None:
    if key == "outbound_departure_window":
        win = require_keys(value, {"start", "end", "confirmed"}, label)
        _iso_date(win["start"], f"{label}.start")
        _iso_date(win["end"], f"{label}.end")
        _require_confirmed(win, label)
    elif key == "return_arrival_by":
        win = require_keys(value, {"latest_local_date", "confirmed"}, label)
        _iso_date(win["latest_local_date"], f"{label}.latest_local_date")
        _require_confirmed(win, label)
    elif key == "departure_days":
        spec = require_keys(value, {"days", "confirmed"}, label)
        bad = sorted({d for d in _str_list(spec["days"], f"{label}.days") if d not in DAY_TOKENS})
        if bad:
            raise UsageError(f"{label}.days has invalid day tokens: {bad}")
        _require_confirmed(spec, label)
    elif key == "cabin":
        spec = require_keys(value, {"value", "confirmed"}, label)
        if spec["value"] not in CABIN_PREFIX:
            raise UsageError(f"{label}.value must be one of {sorted(CABIN_PREFIX)}")
        _require_confirmed(spec, label)
    elif key == "mileage_limit":
        spec = require_keys(value, {"miles"}, label)
        require_int(spec["miles"], f"{label}.miles")


def _validate_preferences(branch: object) -> None:
    if not isinstance(branch, dict):
        raise UsageError("plan.preferences must be an object")
    for key, spec in branch.items():
        _reject_disjoint(key, "plan.preferences")
        if key not in PREFERENCE_KEYS:
            raise UsageError(f"unknown preference key: {key!r}")
        spec = require_keys(spec, {"value", "priority"}, f"plan.preferences[{key!r}]")
        if spec["priority"] not in FACTOR_PRIORITIES:
            raise UsageError(
                f"plan.preferences[{key!r}].priority must be one of {sorted(FACTOR_PRIORITIES)}"
            )
        _validate_pref_value(key, spec["value"], f"plan.preferences[{key!r}].value")


def _validate_constraints(branch: object) -> None:
    if not isinstance(branch, dict):
        raise UsageError("plan.constraints must be an object")
    for key, value in branch.items():
        _reject_disjoint(key, "plan.constraints")
        if key not in CONSTRAINT_KEYS:
            raise UsageError(f"unknown constraint key: {key!r}")
        _validate_constraint_value(key, value, f"plan.constraints[{key!r}]")


def _validate_return(return_spec: object, merged: dict) -> None:
    ret = require_keys(return_spec, set(), "plan.return", optional=frozenset(RETURN_KEYS))
    if "dests" in ret:  # home endpoints — exempt from the destination veto
        require_str_list(ret["dests"], "plan.return.dests")
    if "origins" in ret:
        require_str_list(ret["origins"], "plan.return.origins")
        bad = sorted({a for a in ret["origins"] if a in _vetoed_dests(merged)})
        if bad:
            raise UsageError(f"plan.return.origins vetoed by avoid lists: {bad}")


def _validate_plan(plan: object, merged: dict) -> None:
    plan = require_keys(plan, set(), "plan", optional=frozenset(PLAN_KEYS))
    trip_type = plan.get("trip_type")
    if trip_type is not None and trip_type not in TRIP_TYPES:
        raise UsageError(f"plan.trip_type must be one of {sorted(TRIP_TYPES)}")
    if "origins" in plan:
        require_str_list(plan["origins"], "plan.origins")
    if "sources" in plan:
        require_str_list(plan["sources"], "plan.sources")
    if "buckets" in plan:
        if not isinstance(plan["buckets"], list):
            raise UsageError("plan.buckets must be a list")
        for bucket in plan["buckets"]:
            _validate_bucket(bucket)
    if "program_sweeps" in plan and not isinstance(plan["program_sweeps"], list):
        raise UsageError("plan.program_sweeps must be a list")
    if "hybrid" in plan:
        hybrid = require_keys(plan["hybrid"], set(), "plan.hybrid", optional=frozenset(HYBRID_KEYS))
        if "onward_dests" in hybrid:
            require_str_list(hybrid["onward_dests"], "plan.hybrid.onward_dests")
            bad = sorted({a for a in hybrid["onward_dests"] if a in _vetoed_dests(merged)})
            if bad:
                raise UsageError(f"plan.hybrid.onward_dests vetoed by avoid lists: {bad}")
    if "preferences" in plan:
        _validate_preferences(plan["preferences"])
    if "constraints" in plan:
        _validate_constraints(plan["constraints"])
    if "preferences" in plan and "constraints" in plan:
        both = sorted(set(plan["preferences"]) & set(plan["constraints"]))
        if both:
            raise UsageError(f"keys appear in both preferences and constraints: {both}")
    if "return" in plan:
        if trip_type == "one_way":
            raise UsageError("plan.return is invalid for a one-way trip")
        _validate_return(plan["return"], merged)
    if "lodging" in plan:
        lodging = require_keys(plan["lodging"], set(), "plan.lodging", optional=LODGING_KEYS)
        if "checkout" in lodging:  # an explicit checkout is the only one a one-way/open-jaw carries
            _iso_date(lodging["checkout"], "plan.lodging.checkout")


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
            raise StateConflictError(f"no trip {slug!r} in {trips_dir()}")
        merged = {**current, **patch}
        _validate_trip(merged)
        return merged

    return atomic_update(_trip_json(slug), _mut)


def show(slug: str) -> dict:
    _valid_slug(slug)
    path = _trip_json(slug)
    if not path.exists():
        raise StateConflictError(f"no trip {slug!r} in {trips_dir()}")
    doc = json.loads(path.read_text())
    plan = doc["plan"]
    if plan and "origins" not in plan:
        prefs_doc = prefs.show()
        origins = prefs_doc["origin_airports"] or [prefs_doc["home_airport"]]
        require_str_list(origins, "plan.origins")
        doc["plan"] = {**plan, "origins": origins}
    return doc


def list_() -> list[str]:
    root = trips_dir()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "trip.json").exists())


def done(slug: str) -> dict:
    _valid_slug(slug)

    def _mut(current: dict) -> dict:
        if not current:
            raise StateConflictError(f"no trip {slug!r} in {trips_dir()}")
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
            raise StateConflictError(f"no trip {slug!r} in {trips_dir()}")
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


def _node_index(slug: str) -> dict:
    return {node["id"]: node for node in compile_graph(slug)["nodes"]}


def _upstream_fp(slug: str, node: dict) -> str | None:
    inputs = node["inputs"]
    if not inputs:
        return None
    digest = hashlib.sha256()
    for name in inputs:
        path = _artifact_path(slug, name)
        digest.update(name.encode())
        digest.update(b"\x00")
        digest.update(path.read_bytes() if path.exists() else _ABSENT)
        digest.update(b"\x00")
    return digest.hexdigest()


def capture_upstream_fp(slug: str, key: str) -> str | None:
    """Fingerprint a phase's upstream artifact inputs, captured at work start.

    Mirrors ``capture_inputs_fp`` for artifact inputs: factors.rank and factors.finalize call this
    before reading any input artifact and hand the result to ``phase_done(upstream_fp=...)``, so a
    merge landing mid-run marks the phase stale rather than stamping the post-merge bytes over rows
    read pre-merge. Capture-before-read can only cost an extra refold, never mask a result.
    """
    node = _node_index(slug).get(key)
    if node is None:
        raise UsageError(f"unknown node id: {key!r}")
    return _upstream_fp(slug, node)


def _ttl_ok(record: dict, node: dict, now: Callable[[], datetime]) -> bool:
    ttl = node["ttl_hours"]
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
    node = _node_index(slug).get(key)
    if node is None:  # a phase the current plan no longer compiles is stale
        return False, record
    trip = show(slug)
    prefs_doc = prefs.show()
    fresh = (
        record["inputs_fp"] == capture_inputs_fp(trip, prefs_doc, key)
        and record["upstream_fp"] == _upstream_fp(slug, node)
        and _ttl_ok(record, node, now)
    )
    return fresh, record


def phase_fresh(slug: str, key: str, now: Callable[[], datetime] = utcnow) -> bool:
    return phase_check(slug, key, now=now)[0]


def phase_done(
    slug: str,
    key: str,
    quota_after: int | None = None,
    now: Callable[[], datetime] = utcnow,
    inputs_fp: str | None = None,
    upstream_fp: str | None = None,
) -> dict:
    _valid_slug(slug)
    node = _node_index(slug).get(key)
    if node is None:
        raise UsageError(f"unknown node id: {key!r}")
    if inputs_fp is None:
        inputs_fp = capture_inputs_fp(show(slug), prefs.show(), key)
    if upstream_fp is None:
        upstream_fp = _upstream_fp(slug, node)
    record = {
        "completed_at": now().isoformat(),
        "inputs_fp": inputs_fp,
        "upstream_fp": upstream_fp,
    }
    if quota_after is not None:
        record["quota_after"] = quota_after
    atomic_update(_checkpoints_path(slug), lambda d: {**d, key: record})
    return record


def _validate_sweep_artifact(doc: object, name: str) -> None:
    doc = require_keys(doc, {"provenance", "search_states", "rows"}, name)
    provenance = require_keys(
        doc["provenance"],
        {"source", "fetched_at", "searched", "completeness", "expanded_origins"},
        f"{name}.provenance",
        optional=frozenset({"superseded_rows"}),
    )
    if "superseded_rows" in provenance:
        label = f"{name}.provenance.superseded_rows"
        superseded = require_keys(provenance["superseded_rows"], {"count", "ids"}, label)
        if require_int(superseded["count"], f"{label}.count") <= 0:
            raise UsageError(f"{label}.count must be at least 1")
        require_str_list(superseded["ids"], f"{label}.ids")
        if len(superseded["ids"]) > 50:
            raise UsageError(f"{label}.ids must contain at most 50 entries")
    if not isinstance(doc["search_states"], dict):
        raise UsageError(f"{name}.search_states must be an object")
    if not isinstance(doc["rows"], list):
        raise UsageError(f"{name}.rows must be a list")


def _validate_shortlist_artifact(doc: object, name: str) -> None:
    keys = {"candidates", "considered", "search_states", "leg", "truncation"}
    doc = require_keys(doc, keys, name, optional=frozenset({"provenance"}))
    if "provenance" in doc:
        provenance = require_keys(doc["provenance"], {"superseded_rows"}, f"{name}.provenance")
        label = f"{name}.provenance.superseded_rows"
        superseded = require_keys(provenance["superseded_rows"], {"count"}, label)
        if require_int(superseded["count"], f"{label}.count") <= 0:
            raise UsageError(f"{label}.count must be at least 1")
    if not isinstance(doc["candidates"], list):
        raise UsageError(f"{name}.candidates must be a list")
    if not isinstance(doc["search_states"], dict):
        raise UsageError(f"{name}.search_states must be an object")


def _validate_onward_artifact(doc: object, name: str) -> None:
    require_keys(doc, {"minima", "bridge_pairs"}, name)


def _validate_expand_artifact(doc: object, name: str) -> None:
    doc = require_keys(
        doc,
        {"journeys", "unpaired_outbounds", "gated", "search_states", "leg_states", "provenance"},
        name,
    )
    for key in ("journeys", "unpaired_outbounds", "gated"):
        if not isinstance(doc[key], list):
            raise UsageError(f"{name}.{key} must be a list")
    for key in ("search_states", "leg_states"):
        if not isinstance(doc[key], dict):
            raise UsageError(f"{name}.{key} must be an object")


def _validate_bridge_quote(quote: object, label: str) -> None:
    quote = require_keys(quote, set(BRIDGE_QUOTE_KEYS), label)
    require_str(quote["gateway"], f"{label}.gateway")
    require_str(quote["onward_dest"], f"{label}.onward_dest")
    _iso_date(quote["date"], f"{label}.date")
    require_str(quote["cabin"], f"{label}.cabin")
    require_str(quote["source"], f"{label}.source")
    if not isinstance(quote["price"], (int, float)) or isinstance(quote["price"], bool):
        raise UsageError(f"{label}.price must be a number")
    require_str(quote["currency"], f"{label}.currency")
    require_int(quote["duration_minutes"], f"{label}.duration_minutes")
    require_int(quote["stops"], f"{label}.stops")
    require_str_list(quote["connections"], f"{label}.connections")
    if len(quote["connections"]) != quote["stops"]:
        raise UsageError(f"{label}.connections must have one airport per stop")
    require_str(quote["airline"], f"{label}.airline")
    require_str(quote["flight_number"], f"{label}.flight_number")
    _iso_date(quote["departs_local"], f"{label}.departs_local")
    _iso_date(quote["arrives_local"], f"{label}.arrives_local")


def _validate_bridge_artifact(doc: object, name: str) -> None:
    doc = require_keys(doc, {"quotes"}, name, optional=frozenset({"failures"}))
    if not isinstance(doc["quotes"], list):
        raise UsageError(f"{name}.quotes must be a list")
    for i, quote in enumerate(doc["quotes"]):
        _validate_bridge_quote(quote, f"{name}.quotes[{i}]")


def _validate_finalists_artifact(doc: object, name: str) -> None:
    doc = require_keys(
        doc,
        {
            "trip_type",
            "journeys",
            "notable_stretches",
            "unpaired_leads",
            "search_states",
            "dropped",
        },
        name,
    )
    for key in ("journeys", "notable_stretches", "unpaired_leads", "dropped"):
        if not isinstance(doc[key], list):
            raise UsageError(f"{name}.{key} must be a list")


def _validate_assess_artifact(doc: object, name: str) -> None:
    doc = require_keys(doc, {"journeys", "notable_stretches"}, name)
    if not isinstance(doc["journeys"], dict):
        raise UsageError(f"{name}.journeys must be an object")
    if not isinstance(doc["notable_stretches"], list):
        raise UsageError(f"{name}.notable_stretches must be a list")
    if len(doc["notable_stretches"]) > NOTABLE_PREFERENCE_STRETCH_LIMIT:
        raise UsageError(
            f"{name}.notable_stretches must contain at most "
            f"{NOTABLE_PREFERENCE_STRETCH_LIMIT} entries"
        )
    judged = {f["id"] for f in registry.factors() if f["kind"] in JUDGMENT_FACTOR_KINDS}
    for jid, entry in doc["journeys"].items():
        entry = require_keys(entry, {"verdicts"}, f"{name}.journeys[{jid}]")
        if not isinstance(entry["verdicts"], list):
            raise UsageError(f"{name}.journeys[{jid}].verdicts must be a list")
        for i, verdict in enumerate(entry["verdicts"]):
            label = f"{name}.journeys[{jid}].verdicts[{i}]"
            verdict = require_keys(verdict, {"factor", "leg", "verdict", "evidence"}, label)
            factor = require_str(verdict["factor"], f"{label}.factor")
            if factor not in judged:
                raise UsageError(
                    f"{label}.factor {factor!r} is not a judgment-kind factor; "
                    f"assess may only judge {sorted(judged)}"
                )
            if verdict["verdict"] not in ASSESS_VERDICTS:
                raise UsageError(
                    f"{label}.verdict {verdict['verdict']!r} "
                    f"must be one of {sorted(ASSESS_VERDICTS)}"
                )
            require_str_or_none(verdict["leg"], f"{label}.leg")
            require_str(verdict["evidence"], f"{label}.evidence")
    for i, stretch in enumerate(doc["notable_stretches"]):
        label = f"{name}.notable_stretches[{i}]"
        stretch = require_keys(stretch, {"journey_id", "why"}, label)
        require_str(stretch["journey_id"], f"{label}.journey_id")
        require_str(stretch["why"], f"{label}.why")


def _artifact_validator(leaf: str) -> Callable[[object, str], None] | None:
    if leaf.startswith("sweep") and leaf.endswith(".json"):
        return _validate_sweep_artifact
    if leaf in ("shortlist.json", "shortlist-gateway.json"):
        return _validate_shortlist_artifact
    if leaf == "onward.json":
        return _validate_onward_artifact
    if leaf == "expand.json":
        return _validate_expand_artifact
    if leaf == "bridge.json":
        return _validate_bridge_artifact
    if leaf == "finalists.json":
        return _validate_finalists_artifact
    if leaf == "assess.json":
        return _validate_assess_artifact
    if leaf == "stays.json":
        from getaway import stays  # lazy: stays imports trips at module load

        return stays.validate_stays_doc
    if leaf.startswith("enhance-") and leaf.endswith(".json"):
        from getaway import enhance  # lazy: enhance imports trips at module load

        enhancer = leaf[len("enhance-") : -len(".json")]
        return lambda doc, _label: enhance.validate_enhancer_doc(doc, enhancer)
    return None


def artifact_write(slug: str, name: str, content: str) -> None:
    path = _artifact_path(slug, name)
    leaf = name.rsplit("/", 1)[-1]
    if leaf.endswith(".json"):
        try:
            doc = json.loads(content)
        except json.JSONDecodeError as err:
            raise UsageError(f"artifact {name!r} failed to parse: {err}") from err
        validator = _artifact_validator(leaf)
        if validator is not None:
            validator(doc, name)
    else:
        for line in content.splitlines():
            if line.strip():
                try:
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
    return sorted(
        str(p.relative_to(directory))
        for p in directory.rglob("*")
        if p.is_file() and ARTIFACT_LEAF_RE.match(p.name)
    )


def existing_artifacts(slug: str, names: list[str]) -> list[str]:
    present = set(artifact_list(slug))
    return [name for name in names if name in present]


def _trip_type(plan: dict) -> str:
    trip_type = plan.get("trip_type")
    if trip_type not in TRIP_TYPES:
        raise UsageError("plan.trip_type must be one_way or round_trip before compiling")
    return trip_type


def _node(
    node_id: str,
    kind: str,
    *,
    scope: str,
    inputs: list[str],
    outputs: list[str],
    leg: str | None = None,
    command: list[str] | None = None,
    steps: list[dict] | None = None,
    requires: tuple[str, ...] = (),
    endpoint_source: dict | None = None,
    quota_cost: int | None = None,
) -> dict:
    return {
        "id": node_id,
        "kind": kind,
        "scope": scope,
        "leg": leg,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "ttl_hours": NODE_TTL_HOURS.get(kind),
        "quota_cost": NODE_QUOTA_COST.get(kind, 0) if quota_cost is None else quota_cost,
        "routing": NODE_ROUTING[kind],
        "requires": list(requires),
        "command": command,
        "steps": list(steps or []),
        "endpoint_source": endpoint_source,
    }


def _quota_budget(nodes: list[dict]) -> dict:
    kind_order = {"sweep": 0, "onward": 1, "bridge": 2, "expand": 3}
    leg_order = {"outbound": 0, "return": 1, None: 2}
    costed = [n for n in nodes if n["quota_cost"]]
    costed.sort(key=lambda n: (kind_order.get(n["kind"], 9), leg_order[n["leg"]], n["id"]))
    return {
        "total": sum(n["quota_cost"] for n in costed),
        "nodes": [{"id": n["id"], "quota_cost": n["quota_cost"]} for n in costed],
    }


def compile_graph(slug: str) -> dict:
    from getaway.sweeps import derive_specs  # lazy: sweeps imports trips at module load

    trip = show(slug)
    plan = trip["plan"]
    trip_type = _trip_type(plan)
    prefs_doc = prefs.show()
    has_hybrid = bool(plan.get("hybrid"))
    has_lodging = "lodging" in plan
    # A one-way with no explicit checkout has no return-departure date to derive one from.
    has_stays = has_lodging and (trip_type == "round_trip" or "checkout" in plan["lodging"])
    specs = derive_specs(trip, prefs_doc)
    nodes: list[dict] = []

    for spec in specs:
        label = spec["label"]
        nodes.append(
            _node(
                f"sweep:outbound:{label}",
                "sweep",
                scope="leg",
                leg="outbound",
                inputs=[],
                outputs=[f"legs/outbound/sweep-{label}.json"],
                command=["getaway", "sweep", "run", slug, f"outbound:{label}"],
            )
        )

    ob_shortlist = "legs/outbound/shortlist.json"
    direct_sweeps = [
        f"legs/outbound/sweep-{s['label']}.json" for s in specs if s["label"] != "gateways"
    ]
    nodes.append(
        _node(
            "shortlist:outbound",
            "shortlist",
            scope="leg",
            leg="outbound",
            inputs=direct_sweeps,
            outputs=[ob_shortlist],
            command=["getaway", "shortlist", "run", slug, "--leg", "outbound"],
        )
    )

    if has_hybrid:
        gw_shortlist = "legs/outbound/shortlist-gateway.json"
        nodes.append(
            _node(
                "shortlist:outbound:gateway",
                "shortlist",
                scope="leg",
                leg="outbound",
                inputs=["legs/outbound/sweep-gateways.json"],
                outputs=[gw_shortlist],
                command=["getaway", "shortlist", "run", slug, "--leg", "outbound", "--gateway"],
            )
        )
        onward_sweep = "legs/outbound/sweep-onward.json"
        nodes.append(
            _node(
                "sweep:outbound:onward",
                "sweep",
                scope="leg",
                leg="outbound",
                inputs=[gw_shortlist],
                outputs=[onward_sweep],
                command=["getaway", "sweep", "run", slug, "outbound:onward"],
                endpoint_source={"from": gw_shortlist, "field": "dest"},
            )
        )
        onward = "legs/outbound/onward.json"
        nodes.append(
            _node(
                "onward",
                "onward",
                scope="leg",
                leg="outbound",
                inputs=[gw_shortlist, onward_sweep],
                outputs=[onward],
                command=["getaway", "shortlist", "onward", slug],
            )
        )
        nodes.append(
            _node(
                "bridge",
                "bridge",
                scope="leg",
                leg="outbound",
                inputs=[onward],
                outputs=["legs/outbound/bridge.json"],
                command=["getaway", "bridge", slug],
            )
        )

    if trip_type == "round_trip":
        onward_dests = plan.get("hybrid", {}).get("onward_dests", [])
        ret_sweep = "legs/return/sweep.json"
        nodes.append(
            _node(
                "sweep:return",
                "sweep",
                scope="leg",
                leg="return",
                inputs=[ob_shortlist],
                outputs=[ret_sweep],
                command=["getaway", "sweep", "run", slug, "return"],
                endpoint_source={
                    "from": ob_shortlist,
                    "field": "dest",
                    "union": list(onward_dests),
                    "override": plan.get("return"),
                },
            )
        )
        nodes.append(
            _node(
                "shortlist:return",
                "shortlist",
                scope="leg",
                leg="return",
                inputs=[ret_sweep],
                outputs=["legs/return/shortlist.json"],
                command=["getaway", "shortlist", "run", slug, "--leg", "return"],
            )
        )

    shortlist_inputs = [ob_shortlist]
    if trip_type == "round_trip":
        shortlist_inputs.append("legs/return/shortlist.json")
    if has_hybrid:
        shortlist_inputs.append("legs/outbound/shortlist-gateway.json")

    expand_inputs = list(shortlist_inputs)
    if has_hybrid:
        # Optional cash-hybrid reads: an absent/failed bridge hashes as absent (never blocks
        # directs) and re-runs expand once priced.
        expand_inputs += ["legs/outbound/onward.json", "legs/outbound/bridge.json"]

    nodes.append(
        _node(
            "expand",
            "expand",
            scope="journey",
            inputs=expand_inputs,
            outputs=["expand.json"],
            command=["getaway", "expand", "run", slug],
        )
    )
    nodes.append(
        _node("assess", "assess", scope="journey", inputs=["expand.json"], outputs=["assess.json"])
    )
    nodes.append(
        _node(
            "rank",
            "rank",
            scope="journey",
            inputs=[*shortlist_inputs, "expand.json", "assess.json", "enhance-verify.json"],
            outputs=["rank.json"],
            command=["getaway", "rank", slug],
        )
    )
    finalize_inputs = ["rank.json", "enhance-verify.json"]
    if has_stays:
        finalize_inputs.append("stays.json")
    nodes.append(
        _node(
            "finalize",
            "finalize",
            scope="journey",
            inputs=finalize_inputs,
            outputs=["finalists.json"],
            command=["getaway", "trip", "finalize", slug],
        )
    )
    if has_stays:
        # Agent-shaped (command=None like assess); the walker splices these deterministic steps.
        nodes.append(
            _node(
                "stays",
                "stays",
                scope="journey",
                inputs=["rank.json"],
                outputs=["stays.json"],
                requires=("rooms_session",),
                steps=[
                    {"name": "intervals", "command": ["getaway", "stays", "intervals", slug]},
                    {"name": "ingest", "command": ["getaway", "stays", "ingest", slug]},
                ],
            )
        )

    return {
        "slug": slug,
        "trip_type": trip_type,
        "lodging": has_lodging,
        "requires": ["rooms_session"] if has_stays else [],
        "quota_budget": _quota_budget(nodes),
        "nodes": nodes,
    }


def explain(slug: str, now: Callable[[], datetime] = utcnow) -> dict:
    graph = compile_graph(slug)
    graph["nodes"] = [
        {**node, "fresh": phase_fresh(slug, node["id"], now=now)} for node in graph["nodes"]
    ]
    return graph


def _latest_quota(now: Callable[[], datetime]) -> dict | None:
    from getaway.paths import cache_db
    from getaway.store import NoData, connect

    store = connect(cache_db(), now=now)
    try:
        return store.latest_quota()
    except NoData:
        return None


def status(slug: str, now: Callable[[], datetime] = utcnow) -> dict:
    graph = compile_graph(slug)
    phase_map = {
        node["id"]: "fresh" if phase_fresh(slug, node["id"], now=now) else "stale"
        for node in graph["nodes"]
    }
    return {
        "slug": slug,
        "trip_type": graph["trip_type"],
        "lodging": graph["lodging"],
        "requires": graph["requires"],
        "party": show(slug)["party"],
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
    lines = [f"Trip {slug} — status: {trip['status']} ({st['trip_type']})"]
    if trip["ask"]:
        lines.append(f"Ask: {trip['ask']}")
    lines.append(
        f"Window: {window['start']} to {window['end']} "
        f"({window['trip_length_days']}d), cabin {trip['cabin']}, party {trip['party']}"
    )
    if trip["vibe"]:
        lines.append(f"Vibe: {', '.join(trip['vibe'])}")
    if st["requires"]:
        lines.append(f"Requires: {', '.join(st['requires'])}")
    decisions = trip["decisions"][-5:]
    if decisions:
        lines.append("Recent decisions:")
        lines += [f"  - {d['ts']}: {d['text']}" for d in decisions]
    lines.append("Node freshness:")
    lines += [f"  {key}: {state}" for key, state in st["phase_map"].items()]
    from getaway import enhance  # lazy: enhance imports trips at module load

    lines += enhance.resume_lines(slug)
    expiring = prefs.instrument_list("90d", now=now)
    if expiring:
        lines.append("Instruments expiring within 90d:")
        lines += [f"  {i['type']} — expires {i['expires']}" for i in expiring]
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
@click.option("--quota-after", type=int, default=None)
@map_errors
def _phase_done_cmd(slug: str, key: str, quota_after: int | None) -> None:
    emit(phase_done(slug, key, quota_after))


@trip_group.command("status")
@click.argument("slug")
@map_errors
def _status_cmd(slug: str) -> None:
    emit(status(slug))


@trip_group.command("compile")
@click.argument("slug")
@map_errors
def _compile_cmd(slug: str) -> None:
    emit(compile_graph(slug))


@trip_group.command("explain")
@click.argument("slug")
@map_errors
def _explain_cmd(slug: str) -> None:
    emit(explain(slug))


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
