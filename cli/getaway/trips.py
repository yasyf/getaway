import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import resources

import click

from getaway import prefs, registry
from getaway.constants import (
    AUTO_WIDEN_CALL_BUDGET_PER_LEG,
    CABIN_PREFIX,
    CONTINENTS,
    DISJOINT_DURABLE_PREF_KEYS,
    GENERATION_CUTTING_COMPLETENESS,
    NODE_ROUTING,
    NODE_TTL_HOURS,
    NOTABLE_PREFERENCE_STRETCH_LIMIT,
    TUNING_KEYS,
    tuned,
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
IATA_RE = re.compile(r"^[A-Z]{3}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DISCOVER_AIRPORTS = 12
SCOUT_WHY_MAX = 200
RESERVED_BUCKET_NAMES = frozenset({"gateways", "onward"})
RESERVED_KEYS = frozenset({"slug", "created"})
PLAN_KEYS = frozenset(
    {
        "legs",
        "sources",
        "preferences",
        "constraints",
        "lodging",
        "tuning",
    }
)
LEG_KEYS = frozenset(
    {
        "id",
        "origins",
        "dests",
        "mode",
        "window",
        "stay_nights",
        "cabin",
        "optional",
        "buckets",
        "program_sweeps",
        "role",
    }
)
LEG_MODES = frozenset({"award", "cash", "either"})
LEG_ID_RE = ARTIFACT_SEGMENT_RE  # leg ids become artifact path segments (legs/<id>/…)
ORIGINS_MARKER = "$origins"
V2_PLAN_KEYS = frozenset({"trip_type", "hybrid", "return", "origins", "buckets", "program_sweeps"})
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
    if not SLUG_RE.fullmatch(slug):
        raise UsageError(f"invalid trip slug: {slug!r}")
    return slug


def _trip_json(slug: str):
    return trip_dir(slug) / "trip.json"


def _checkpoints_path(slug: str):
    return trip_dir(slug) / "checkpoints.json"


def _artifact_path(slug: str, name: str):
    _valid_slug(slug)
    *dirs, leaf = name.split("/")
    if not ARTIFACT_LEAF_RE.fullmatch(leaf) or not all(
        ARTIFACT_SEGMENT_RE.fullmatch(d) for d in dirs
    ):
        raise UsageError(f"invalid artifact name: {name!r}")
    return trip_dir(slug).joinpath("artifacts", *name.split("/"))


def _factor_ids() -> set[str]:
    data = json.loads((resources.files("getaway") / "data" / "factors.json").read_text())
    return {f["id"] for f in data["factors"]}


def _validate_bucket(bucket: object) -> None:
    bucket = require_keys(bucket, {"name", "dests"}, "plan.buckets row")
    name = require_str(bucket["name"], "plan.buckets.name")
    if not BUCKET_NAME_RE.fullmatch(name):
        raise UsageError(f"plan.buckets.name must match {BUCKET_NAME_RE.pattern!r}: {name!r}")
    if name in RESERVED_BUCKET_NAMES:
        raise UsageError(f"plan.buckets.name is a reserved label: {name!r}")
    require_str_list(bucket["dests"], "plan.buckets.dests")
    if not bucket["dests"]:
        raise UsageError("plan.buckets.dests must be a non-empty list")


def _iso_date(value: object, label: str) -> None:
    # DATE-ONLY: datetime/tz forms would poison window comparisons with a naive-vs-aware TypeError.
    text = require_str(value, label)
    if not ISO_DATE_RE.fullmatch(text):
        raise UsageError(f"{label} must be a YYYY-MM-DD ISO date: {value!r}")
    try:
        datetime.fromisoformat(text)
    except ValueError as err:
        raise UsageError(f"{label} is not a valid ISO date: {value!r}") from err


def _iso_local_datetime(value: object, label: str) -> None:
    # Bridge quote clocks are local wall-clock datetimes (YYYY-MM-DDThh:mm), not bare dates.
    text = require_str(value, label)
    try:
        datetime.fromisoformat(text)
    except ValueError as err:
        raise UsageError(f"{label} is not an ISO datetime: {value!r}") from err


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


def _conventional_leg_ids(legs: list) -> list[str]:
    """Effective ids for an ordered leg list: explicit ``id``, else the conventional default.

    First leg defaults to ``outbound`` and a two-leg plan's second to ``return``; every other
    leg names itself. Ids are convention, never logic — only position and ``$origins``-direction
    are structural (doc 49100ad).
    """
    n = len(legs)
    ids: list[str] = []
    for index, leg in enumerate(legs):
        explicit = leg.get("id")
        if explicit is not None:
            if not isinstance(explicit, str):
                raise UsageError(f"plan.legs[{index}].id must be a string")
            ids.append(explicit)
        elif index == 0:
            ids.append("outbound")
        elif index == n - 1 and n == 2:
            ids.append("return")
        else:
            raise UsageError(f"plan.legs[{index}] requires an explicit id")
    return ids


def _require_label_component(value: object, label: str) -> str:
    """A component that folds into a sweep label (node id, artifact leaf, command token) must
    match the leaf grammar bucket names already enforce — lowercase alnum + hyphen — so nothing
    can escape into a path segment or corrupt the ``leg:label`` spec. Non-empty by construction."""
    text = require_str(value, label)
    if not BUCKET_NAME_RE.fullmatch(text):
        raise UsageError(f"{label} must match {BUCKET_NAME_RE.pattern!r}: {text!r}")
    return text


def _require_continent(value: object, label: str) -> str:
    """A program-sweep region is a seats.aero API operand, not a free label: one of the six
    continent names (docs/seats-aero-api.md). It is sent verbatim to /availability and slugified
    only when it folds into a sweep label."""
    text = require_str(value, label)
    if text not in CONTINENTS:
        raise UsageError(f"{label} must be one of {sorted(CONTINENTS)}: {text!r}")
    return text


def _validate_program_sweep(sweep: object, label: str) -> None:
    optional = frozenset({"dest_region", "origin_region"})
    sweep = require_keys(sweep, {"source"}, label, optional=optional)
    _require_label_component(sweep["source"], f"{label}.source")
    regions = [key for key in ("dest_region", "origin_region") if key in sweep]
    if not regions:
        raise UsageError(f"{label} needs dest_region or origin_region")
    for key in regions:
        _require_continent(sweep[key], f"{label}.{key}")


def _validate_leg(leg: object, index: int, leg_id: str, merged: dict) -> None:
    label = f"plan.legs[{index}]"
    leg = require_keys(leg, set(), label, optional=LEG_KEYS)
    if not LEG_ID_RE.fullmatch(leg_id):
        raise UsageError(f"{label}.id must match {LEG_ID_RE.pattern!r}: {leg_id!r}")
    mode = leg.get("mode", "award")
    if mode not in LEG_MODES:
        raise UsageError(f"{label}.mode must be one of {sorted(LEG_MODES)}")
    if mode == "cash" and ("buckets" in leg or "program_sweeps" in leg):
        raise UsageError(
            f"{label} is cash-only; buckets/program_sweeps are award-lane groupings, "
            f"not valid on a cash leg"
        )
    is_first = index == 0
    if "origins" in leg:
        require_str_list(leg["origins"], f"{label}.origins")
        if not leg["origins"]:
            raise UsageError(f"{label}.origins must be a non-empty list")
        if not is_first:  # an explicit later-leg origin is a place reached — veto it
            bad = sorted({a for a in leg["origins"] if a in _vetoed_dests(merged)})
            if bad:
                raise UsageError(f"{label}.origins vetoed by avoid lists: {bad}")
    _validate_leg_dests(leg, is_first, mode, label, merged)
    if "window" in leg:
        win = require_keys(leg["window"], {"start", "end"}, f"{label}.window")
        _iso_date(win["start"], f"{label}.window.start")
        _iso_date(win["end"], f"{label}.window.end")
        if datetime.fromisoformat(win["start"]) > datetime.fromisoformat(win["end"]):
            raise UsageError(f"{label}.window.start must be <= end")
    if "stay_nights" in leg:
        stay = require_keys(leg["stay_nights"], {"min", "max"}, f"{label}.stay_nights")
        low = require_int(stay["min"], f"{label}.stay_nights.min")
        high = require_int(stay["max"], f"{label}.stay_nights.max")
        if low < 1 or high < 1:
            raise UsageError(f"{label}.stay_nights must be positive")
        if low > high:
            raise UsageError(f"{label}.stay_nights.min must be <= max")
    if "cabin" in leg and leg["cabin"] not in CABIN_PREFIX:
        raise UsageError(f"{label}.cabin must be one of {sorted(CABIN_PREFIX)}")
    if "optional" in leg and not isinstance(leg["optional"], bool):
        raise UsageError(f"{label}.optional must be a boolean")
    if "buckets" in leg:
        if not isinstance(leg["buckets"], list):
            raise UsageError(f"{label}.buckets must be a list")
        for bucket in leg["buckets"]:
            _validate_bucket(bucket)
    if "program_sweeps" in leg:
        if not isinstance(leg["program_sweeps"], list):
            raise UsageError(f"{label}.program_sweeps must be a list")
        for i, sweep in enumerate(leg["program_sweeps"]):
            _validate_program_sweep(sweep, f"{label}.program_sweeps[{i}]")
    if "role" in leg:
        require_str(leg["role"], f"{label}.role")


def _validate_concrete_dests(dests: list, label: str, merged: dict) -> None:
    require_str_list(dests, f"{label}.dests")
    if not dests:
        raise UsageError(f"{label}.dests must be a non-empty list")
    if ORIGINS_MARKER in dests:
        raise UsageError(
            f"{label}.dests: {ORIGINS_MARKER!r} is a whole-value marker, not a list member"
        )
    bad = sorted({a for a in dests if a in _vetoed_dests(merged)})
    if bad:
        raise UsageError(f"{label}.dests vetoed by avoid lists: {bad}")


def _validate_leg_dests(leg: dict, is_first: bool, mode: str, label: str, merged: dict) -> None:
    dests = leg.get("dests")
    if mode == "cash":
        # A cash leg must anchor its successor concretely: a non-empty IATA list or whole-value
        # $origins. No discover dests; groupings already rejected in _validate_leg.
        if dests == ORIGINS_MARKER:
            if is_first:
                raise UsageError(
                    f"{label}.dests {ORIGINS_MARKER!r} is only valid on a non-first leg"
                )
            return
        if not isinstance(dests, list) or not dests:
            raise UsageError(
                f"{label} is cash-only and needs a non-empty dests list or {ORIGINS_MARKER!r}"
            )
        _validate_concrete_dests(dests, label, merged)
        return
    has_groupings = bool(leg.get("buckets")) or bool(leg.get("program_sweeps"))
    if dests is None:
        if not has_groupings:
            raise UsageError(f"{label} needs dests, buckets, or program_sweeps")
    elif dests == ORIGINS_MARKER:
        if is_first:
            raise UsageError(f"{label}.dests {ORIGINS_MARKER!r} is only valid on a non-first leg")
    elif isinstance(dests, dict):  # discover — a scout node proposes the dest airports (P3)
        if mode != "award":
            raise UsageError(f"{label}.dests discover is only valid on an award leg")
        # Scout ADDS endpoints: its airports ride alongside any declared buckets/program_sweeps,
        # each grouping keeping its own concrete sweep beside the scout-fed bare one.
        discover = require_keys(dests, {"discover"}, f"{label}.dests")
        spec = require_keys(
            discover["discover"], {"brief", "max_airports"}, f"{label}.dests.discover"
        )
        if not require_str(spec["brief"], f"{label}.dests.discover.brief").strip():
            raise UsageError(f"{label}.dests.discover.brief must be non-empty")
        cap = require_int(spec["max_airports"], f"{label}.dests.discover.max_airports")
        if cap < 1 or cap > MAX_DISCOVER_AIRPORTS:
            raise UsageError(
                f"{label}.dests.discover.max_airports must be 1..{MAX_DISCOVER_AIRPORTS}"
            )
    else:
        _validate_concrete_dests(dests, label, merged)


def _validate_legs(legs: object, merged: dict) -> None:
    if not isinstance(legs, list) or not legs:
        raise UsageError("plan.legs must be a non-empty list")
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            raise UsageError(f"plan.legs[{index}] must be an object")
    ids = _conventional_leg_ids(legs)
    if len(set(ids)) != len(ids):
        raise UsageError(f"plan.legs ids must be unique: {sorted(ids)}")
    for index, leg in enumerate(legs):
        _validate_leg(leg, index, ids[index], merged)
    _validate_window_order(legs)


def _validate_window_order(legs: list) -> None:
    """Legs run forward in time: no later leg's window may end before an earlier leg's begins."""
    latest_start: datetime | None = None
    latest_start_index = -1
    for index, leg in enumerate(legs):
        win = leg.get("window")
        if not isinstance(win, dict):
            continue
        start = datetime.fromisoformat(win["start"])
        end = datetime.fromisoformat(win["end"])
        if latest_start is not None and end < latest_start:
            raise UsageError(
                f"plan.legs[{index}].window.end precedes plan.legs[{latest_start_index}]"
                f".window.start: legs must run forward in time"
            )
        if latest_start is None or start > latest_start:
            latest_start, latest_start_index = start, index


def _validate_tuning(branch: object) -> None:
    """Per-trip search-width overrides: each key is a positive int; unknown keys reject. An absent
    key leaves the consumer on its constant default (constants.py stays the single source)."""
    tuning = require_keys(branch, set(), "plan.tuning", optional=TUNING_KEYS)
    for key, value in tuning.items():
        if require_int(value, f"plan.tuning.{key}") < 1:
            raise UsageError(f"plan.tuning.{key} must be a positive integer")


def _validate_plan(plan: object, merged: dict) -> None:
    plan = require_keys(plan, set(), "plan", optional=PLAN_KEYS)
    if "legs" in plan:
        _validate_legs(plan["legs"], merged)
    if "sources" in plan:
        require_str_list(plan["sources"], "plan.sources")
    if "preferences" in plan:
        _validate_preferences(plan["preferences"])
    if "constraints" in plan:
        _validate_constraints(plan["constraints"])
    if "preferences" in plan and "constraints" in plan:
        both = sorted(set(plan["preferences"]) & set(plan["constraints"]))
        if both:
            raise UsageError(f"keys appear in both preferences and constraints: {both}")
    if "lodging" in plan:
        lodging = require_keys(plan["lodging"], set(), "plan.lodging", optional=LODGING_KEYS)
        if "checkout" in lodging:  # an explicit checkout is the only one a one-way/open-jaw carries
            _iso_date(lodging["checkout"], "plan.lodging.checkout")
    if "tuning" in plan:
        _validate_tuning(plan["tuning"])


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
        _reject_v2_plan(merged["plan"])  # a patch over a stored v2 plan rejects like show()/compile
        _validate_trip(merged)
        return merged

    return atomic_update(_trip_json(slug), _mut)


def _reject_v2_plan(plan: object) -> None:
    """A stored pre-cutover plan (trip_type/hybrid/return/origins/buckets) can't be read: no
    migration. Fail loud at the read boundary, naming the offending keys and the remedy."""
    if not isinstance(plan, dict):
        raise UsageError("plan must be an object")
    found = sorted(set(plan) & V2_PLAN_KEYS)
    if found:
        raise UsageError(
            f"plan uses removed v2 keys {found} (a pre-cutover trip); "
            f"re-declare it as plan.legs via 'trip set'."
        )


def _materialize_plan(plan: dict, prefs_doc: dict) -> dict:
    """Fill conventional leg ids, the default award mode, and the first leg's origins.

    The stored doc stays sparse; every consumer reads the materialized plan so leg ids and the
    ``$origins`` anchor are always explicit.
    """
    ids = _conventional_leg_ids(plan["legs"])
    legs = [
        {**leg, "id": leg_id, "mode": leg.get("mode", "award")}
        for leg_id, leg in zip(ids, plan["legs"])
    ]
    first = legs[0]
    if "origins" not in first:
        origins = prefs_doc["origin_airports"] or [prefs_doc["home_airport"]]
        require_str_list(origins, "plan.legs[0].origins")
        first["origins"] = origins
    return {**plan, "legs": legs}


def show(slug: str) -> dict:
    _valid_slug(slug)
    path = _trip_json(slug)
    if not path.exists():
        raise StateConflictError(f"no trip {slug!r} in {trips_dir()}")
    doc = json.loads(path.read_text())
    plan = doc["plan"]
    _reject_v2_plan(plan)
    if plan.get("legs"):
        doc["plan"] = _materialize_plan(plan, prefs.show())
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
    if not isinstance(doc["search_states"], dict):
        raise UsageError(f"{name}.search_states must be an object")
    for endpoint, state in doc["search_states"].items():
        if not isinstance(state, dict):
            raise UsageError(f"{name}.search_states[{endpoint!r}] must be an object")
    if not isinstance(doc["rows"], list):
        raise UsageError(f"{name}.rows must be a list")
    if "superseded_rows" in provenance:
        label = f"{name}.provenance.superseded_rows"
        superseded = require_keys(provenance["superseded_rows"], {"count", "ids"}, label)
        count = require_int(superseded["count"], f"{label}.count")
        if count <= 0:
            raise UsageError(f"{label}.count must be at least 1")
        ids = superseded["ids"]
        require_str_list(ids, f"{label}.ids")
        if len(set(ids)) != len(ids):
            raise UsageError(f"{label}.ids must not repeat an id")
        if len(ids) != min(count, 50):
            raise UsageError(f"{label}.ids must list min(count, 50) entries")
        if not isinstance(provenance["searched"], list):
            raise UsageError(f"{name}.provenance.searched must be a list")
        if not provenance["searched"]:
            raise UsageError(f"{label} cannot accompany a sweep that searched nothing")
        completeness = require_str(
            provenance["completeness"], f"{name}.provenance.completeness"
        )
        if completeness not in GENERATION_CUTTING_COMPLETENESS:
            raise UsageError(f"{label} requires a complete sweep, not {completeness!r}")
        for endpoint, state in doc["search_states"].items():
            endpoint_state = require_str(
                state.get("state"), f"{name}.search_states[{endpoint!r}].state"
            )
            if endpoint_state not in GENERATION_CUTTING_COMPLETENESS:
                raise UsageError(
                    f"{label} requires every endpoint fully searched, not "
                    f"{endpoint!r}={endpoint_state!r}"
                )
        row_ids: set[str] = set()
        for index, row in enumerate(doc["rows"]):
            row_label = f"{name}.rows[{index}]"
            if not isinstance(row, dict):
                raise UsageError(f"{row_label} must be an object")
            row_ids.add(require_str(row.get("ID"), f"{row_label}.ID"))
        if row_ids.intersection(ids):
            raise UsageError(f"{label}.ids must be disjoint from the sweep's own rows")


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
        optional=frozenset({"leads", "manual_rejected"}),
    )
    for key in ("journeys", "unpaired_outbounds", "gated"):
        if not isinstance(doc[key], list):
            raise UsageError(f"{name}.{key} must be a list")
    for key in ("leads", "manual_rejected"):
        if key in doc and not isinstance(doc[key], list):
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
    currency = require_str(quote["currency"], f"{label}.currency")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise UsageError(f"{label}.currency must be three uppercase ASCII letters")
    require_int(quote["duration_minutes"], f"{label}.duration_minutes")
    require_int(quote["stops"], f"{label}.stops")
    require_str_list(quote["connections"], f"{label}.connections")
    if len(quote["connections"]) != quote["stops"]:
        raise UsageError(f"{label}.connections must have one airport per stop")
    require_str(quote["airline"], f"{label}.airline")
    require_str(quote["flight_number"], f"{label}.flight_number")
    _iso_local_datetime(quote["departs_local"], f"{label}.departs_local")
    _iso_local_datetime(quote["arrives_local"], f"{label}.arrives_local")


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
        optional=frozenset({"truncation"}),
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


def _read_json_artifact(slug: str, name: str) -> dict | None:
    path = _artifact_path(slug, name)
    return json.loads(path.read_text()) if path.exists() else None


def _manual_award_ids(slug: str, leg_id: str) -> set[str]:
    doc = _read_json_artifact(slug, f"legs/{leg_id}/shortlist.json")
    return {c["id"] for c in doc["candidates"]} if doc is not None else set()


def _manual_cash_keys(slug: str, leg_id: str) -> set[tuple[str, str, str]]:
    doc = _read_json_artifact(slug, f"legs/{leg_id}/bridge.json")
    if doc is None:
        return set()
    return {(q["gateway"], q["onward_dest"], q["date"]) for q in doc["quotes"]}


def _validate_manual_candidate(slug: str, leg: dict, candidate: object, label: str) -> None:
    leg_id = leg["id"]
    mode = leg["mode"]
    if isinstance(candidate, str):
        if mode not in ("award", "either"):
            raise UsageError(
                f"{label}.candidate {candidate!r} is an award id but leg {leg_id!r} is cash-only"
            )
        if candidate not in _manual_award_ids(slug, leg_id):
            raise UsageError(
                f"{label}.candidate {candidate!r} is not in leg {leg_id!r}'s shortlist"
            )
        return
    key = require_keys(candidate, {"gateway", "onward_dest", "date"}, f"{label}.candidate")
    if mode not in ("cash", "either"):
        raise UsageError(f"{label}.candidate is a cash quote but leg {leg_id!r} is award-only")
    require_str(key["gateway"], f"{label}.candidate.gateway")
    require_str(key["onward_dest"], f"{label}.candidate.onward_dest")
    _iso_date(key["date"], f"{label}.candidate.date")
    triple = (key["gateway"], key["onward_dest"], key["date"])
    if triple not in _manual_cash_keys(slug, leg_id):
        raise UsageError(
            f"{label}.candidate {triple} is not a priced quote in leg {leg_id!r}'s bridge"
        )


def _manual_chain_variant(legs: list[dict], ids: list[str]) -> tuple[list[int], str]:
    """Classify a manual chain's declared leg ids (each already a known plan leg) as a variant
    of the current plan. Returns ``(positions, kind)``: ``positions`` are the plan indices in
    declared order — the chain's leg subset, the VARIANT threaded through composition — and
    ``kind`` is ``""`` for a valid variant (a strictly increasing subsequence that covers every
    mandatory leg and whose first covered leg is not the homeward ``$origins`` leg, R-D), else
    ``"order"`` (not in plan order or a repeat), ``"missing"`` (a mandatory leg absent), or
    ``"home"`` (opens on the homeward leg). A plan with no optional legs collapses
    ``"order"``/``"missing"`` onto the every-leg rule at the caller's message layer."""
    leg_index = {leg["id"]: i for i, leg in enumerate(legs)}
    positions = [leg_index[i] for i in ids]
    if any(a >= b for a, b in zip(positions, positions[1:])):
        return positions, "order"
    covered = set(ids)
    if any(not leg.get("optional") and leg["id"] not in covered for leg in legs):
        return positions, "missing"
    if legs[positions[0]].get("dests") == ORIGINS_MARKER:
        return positions, "home"
    return positions, ""


def _validate_manual_artifact(slug: str, doc: object, name: str) -> None:
    """The manual-chain artifact: a list of explicit candidate chains. Each chain must cover every
    MANDATORY plan leg once in plan order and may include or skip any OPTIONAL legs (included legs
    in plan order) — the chain's leg subset is its variant. Leg ids, order, coverage, and candidate
    references resolve at write or the write fails loud — continuity is the compile layer's honesty
    gate."""
    legs = show(slug)["plan"].get("legs")
    if not legs:
        raise UsageError(f"{name}: cannot validate manual chains before plan.legs is set")
    leg_by_id = {leg["id"]: leg for leg in legs}
    plan_ids = [leg["id"] for leg in legs]
    mandatory_ids = [leg["id"] for leg in legs if not leg.get("optional")]
    if not isinstance(doc, list):
        raise UsageError(f"{name} must be a list of candidate chains")
    for c, chain in enumerate(doc):
        label = f"{name}[{c}]"
        if not isinstance(chain, list) or not chain:
            raise UsageError(f"{label} must be a non-empty list of {{leg_id, candidate}} entries")
        resolved: list[tuple[dict, object]] = []
        for e, entry in enumerate(chain):
            entry = require_keys(entry, {"leg_id", "candidate"}, f"{label}[{e}]")
            leg_id = require_str(entry["leg_id"], f"{label}[{e}].leg_id")
            if leg_id not in leg_by_id:
                raise UsageError(f"{label}[{e}].leg_id {leg_id!r} is not a plan leg")
            resolved.append((leg_by_id[leg_id], entry["candidate"]))
        ids = [leg["id"] for leg, _ in resolved]
        _, kind = _manual_chain_variant(legs, ids)
        if kind in ("order", "missing"):
            if len(mandatory_ids) == len(plan_ids):
                raise UsageError(
                    f"{label} must list every leg once in plan order {plan_ids}, got {ids}"
                )
            if kind == "order":
                raise UsageError(
                    f"{label} legs must be a subsequence of plan order {plan_ids}, got {ids}"
                )
            missing = [m for m in mandatory_ids if m not in ids]
            raise UsageError(
                f"{label} must cover every mandatory leg {mandatory_ids} once in plan order, "
                f"missing {missing}, got {ids}"
            )
        if kind == "home":
            raise UsageError(
                f"{label} opens on the homeward leg {ids[0]!r} ({ORIGINS_MARKER}); "
                "a manual chain must start with a real departure"
            )
        for e, (leg, candidate) in enumerate(resolved):
            _validate_manual_candidate(slug, leg, candidate, f"{label}[{e}]")


def _discover_leg(slug: str, leg_id: str, name: str) -> dict:
    legs = show(slug)["plan"].get("legs")
    if not legs:
        raise UsageError(f"{name}: cannot validate a scout artifact before plan.legs is set")
    leg = next((entry for entry in legs if entry["id"] == leg_id), None)
    if leg is None or not isinstance(leg.get("dests"), dict):
        raise UsageError(f"{name}: leg {leg_id!r} is not a discover leg")
    return leg


def _validate_scout_artifact(slug: str, doc: object, name: str) -> None:
    """The scout artifact ``legs/<leg-id>/scout.json``: an agent-proposed list of ``{airport, why}``
    hub candidates for a discover leg, capped at the leg's ``max_airports``. Airports feed the leg's
    sweep endpoints; a malformed entry, a non-IATA code, or an over-cap list rejects loudly at
    write, so the sweep only ever consumes validated airports."""
    parts = name.split("/")
    if len(parts) != 3 or parts[0] != "legs" or parts[2] != "scout.json":
        raise UsageError(f"{name}: a scout artifact must be legs/<leg-id>/scout.json")
    leg = _discover_leg(slug, parts[1], name)
    cap = leg["dests"]["discover"]["max_airports"]
    if not isinstance(doc, list):
        raise UsageError(f"{name} must be a list of {{airport, why}} entries")
    if len(doc) > cap:
        raise UsageError(
            f"{name} lists {len(doc)} airports, over the leg's max_airports {cap}"
        )
    for i, entry in enumerate(doc):
        label = f"{name}[{i}]"
        entry = require_keys(entry, {"airport", "why"}, label)
        airport = require_str(entry["airport"], f"{label}.airport")
        if not IATA_RE.fullmatch(airport):
            raise UsageError(f"{label}.airport must be a 3-letter IATA code: {airport!r}")
        if len(require_str(entry["why"], f"{label}.why")) > SCOUT_WHY_MAX:
            raise UsageError(f"{label}.why must be at most {SCOUT_WHY_MAX} characters")


def _artifact_validator(leaf: str) -> Callable[[object, str], None] | None:
    if leaf.startswith("sweep") and leaf.endswith(".json"):
        return _validate_sweep_artifact
    if leaf == "shortlist.json":
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
        if name == "legs/manual.json":
            _validate_manual_artifact(slug, doc, name)
        if leaf == "scout.json":
            _validate_scout_artifact(slug, doc, name)
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
        if p.is_file() and ARTIFACT_LEAF_RE.fullmatch(p.name)
    )


def existing_artifacts(slug: str, names: list[str]) -> list[str]:
    present = set(artifact_list(slug))
    return [name for name in names if name in present]


def _targets_origins(plan: dict) -> bool:
    """The return-side gate: does the last intent fly back to ``$origins`` (doc 49100ad)?

    Replaces the ``_trip_type != "one_way"`` gate — still plan-derived, same call shape.
    """
    legs = plan.get("legs")
    if not legs:
        raise UsageError("plan.legs must be a non-empty list before compiling")
    return legs[-1].get("dests") == ORIGINS_MARKER


def _shape_label(plan: dict) -> str:
    legs = plan.get("legs")
    if not legs:
        raise UsageError("plan.legs must be a non-empty list")
    if len(legs) == 1:
        return "one_way"
    return "round_trip" if _targets_origins(plan) else "open_jaw"


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
        "quota_cost": quota_cost if quota_cost is not None else 0,
        "routing": NODE_ROUTING[kind],
        "requires": list(requires),
        "command": command,
        "steps": list(steps or []),
        "endpoint_source": endpoint_source,
    }


def _quota_budget(nodes: list[dict], leg_order: dict[str, int]) -> dict:
    kind_order = {"sweep": 0, "onward": 1, "bridge": 2, "expand": 3}
    # Journey-scoped nodes (leg=None) settle after every leg's costed work.
    last = len(leg_order)
    costed = [n for n in nodes if n["quota_cost"]]

    def order(n: dict) -> tuple:
        return (kind_order.get(n["kind"], 9), leg_order.get(n["leg"], last), n["id"])

    costed.sort(key=order)
    return {
        "total": sum(n["quota_cost"] for n in costed),
        "nodes": [{"id": n["id"], "quota_cost": n["quota_cost"]} for n in costed],
    }


def _leg_sweep_labels(leg: dict, leg_id: str) -> list[str | None]:
    """Sweep labels for an award/either leg: one per bucket, one per program sweep, plus a bare
    scout-fed sweep (label ``None``) for a discover leg, else a bare single sweep (label ``None``).
    Components are leaf-grammar-validated upstream. Labels must be unique within the leg — two
    groupings folding to one label would alias one node id and artifact.
    """
    labeled: list[tuple[str, str]] = [
        (bucket["name"], f"bucket {bucket['name']!r}") for bucket in leg.get("buckets", [])
    ]
    for sweep in leg.get("program_sweeps", []):
        # origin_region takes a "from-" infix so a source's dest and origin sweeps over one
        # continent stay distinct. Slug is total over the closed continent vocabulary.
        if "dest_region" in sweep:
            label = f"{sweep['source']}-{sweep['dest_region'].lower().replace(' ', '-')}"
        else:
            label = f"{sweep['source']}-from-{sweep['origin_region'].lower().replace(' ', '-')}"
        labeled.append((label, f"program_sweep {sweep!r}"))
    seen: dict[str, str] = {}
    for label, source in labeled:
        if label in seen:
            raise UsageError(
                f"plan.legs[{leg_id!r}] derives sweep label {label!r} twice: "
                f"{seen[label]} vs {source}"
            )
        seen[label] = source
    labels: list[str | None] = [label for label, _ in labeled]
    if isinstance(leg.get("dests"), dict):  # discover: the scout-fed bare sweep joins the groupings
        labels.append(None)
    return labels or [None]


def _leg_override(leg: dict) -> dict | None:
    """Explicit endpoints that override the chained defaults (open jaw / non-``$origins`` home)."""
    override: dict = {}
    if "origins" in leg:
        override["origins"] = leg["origins"]
    dests = leg.get("dests")
    if isinstance(dests, list):
        override["dests"] = dests
    return override or None


def _leg_declared_dests(leg: dict, home_origins: list[str]) -> list[str]:
    """A leg's declared concrete dests — the cash-reachable landings it forwards to its successor's
    union. ``$origins`` resolves to the materialized first-leg origins (a cash-home leg anchors its
    successor at home, not on []); a leg that declares its dests only through buckets forwards those
    bucket landings (program_sweeps carry regions, not concrete dests, and forward nothing)."""
    dests = leg.get("dests")
    if dests == ORIGINS_MARKER:
        return list(home_origins)
    if isinstance(dests, list):
        return [d for d in dests if isinstance(d, str)]
    landings: list[str] = []
    for bucket in leg.get("buckets", []):
        for dest in bucket["dests"]:
            if dest not in landings:
                landings.append(dest)
    return landings


def _chain_source(
    leg: dict, prior_leg: dict | None, prior_shortlist: str | None, home_origins: list[str]
) -> dict | None:
    """Where a non-first leg draws its origins from (doc 49100ad chaining):

    ``from`` the prior leg's shortlist iff the prior leg has an award lane (award/either);
    ``union`` the prior leg's declared dests iff it has a cash lane (cash/either) — so an
    either-mode hop carries its cash-reachable dests forward and a leg after a pure-cash leg
    anchors on that leg's declared dests rather than silently chaining past it.
    """
    if prior_leg is None:
        return None
    prior_award = prior_leg["mode"] in ("award", "either")
    prior_cash = prior_leg["mode"] in ("cash", "either")
    source: dict = {}
    if prior_award:
        source["from"] = prior_shortlist
    source["field"] = "dest"
    source["union"] = _leg_declared_dests(prior_leg, home_origins) if prior_cash else []
    source["override"] = _leg_override(leg)
    return source


def _skip_contribution(
    endpoint_source: dict | None, leg: dict, prior_leg: dict | None
) -> list[dict]:
    """The pre-boundary a successor re-anchors on when this ``optional`` leg is skipped (skip
    transparency, R-A) — NOT the leg's own departure. When an optional leg vanishes the runtime
    chains its successor from the PRE-optional boundary: for ``i > 0`` that is leg ``i-1``'s
    landings (this leg's own non-override ``from``/``union``) plus leg ``i-1``'s own skip
    contribution when it too is optional (folding consecutive optionals); the first leg contributes
    the trip's declared home origins. The leg's own explicit-origins override NEVER enters — those
    anchor only its own full-variant sweep and vanish with the leg (MAJOR-1). Each ``from`` source
    also carries the pre-boundary's ``stay_nights`` so the successor's window envelope can search
    the skip variant's stay-valid departures off those arrivals (MAJOR-2)."""
    if endpoint_source is None:
        return [{"union": list(leg["origins"])}]
    own: dict = {}
    if "from" in endpoint_source:
        own["from"] = endpoint_source["from"]
        own["field"] = endpoint_source["field"]
        if prior_leg is not None and "stay_nights" in prior_leg:
            own["stay_nights"] = prior_leg["stay_nights"]
    if endpoint_source.get("union"):
        own["union"] = list(endpoint_source["union"])
    sources = [own] if own else []
    return sources + [dict(src) for src in endpoint_source.get("skip_sources", [])]


def resolve_source_airports(slug: str, source: dict) -> set[str]:
    """A chain/skip source's departure airports: its ``from`` shortlist's ``field`` values unioned
    with its carried ``union`` dests. Shared by the sweep (:func:`sweeps._chained_endpoints`) and
    cash (:func:`shortlist._gateway_dates`) lanes so skip transparency resolves identically — both
    for airports (here) and for the temporal half: each lane resolves a skip source's departure
    dates exactly as it resolves its own chain source, over the shared stay-shifted window
    (:func:`sweeps._skip_source_window`)."""
    airports = set(source.get("union", []))
    if "from" in source:
        prior = json.loads(artifact_read(slug, source["from"]))
        for cand in prior["candidates"]:
            airports.add(cand[source["field"]])
    return airports


def compile_graph(slug: str) -> dict:
    trip = show(slug)
    plan = trip["plan"]
    if not plan.get("legs"):
        raise UsageError("plan.legs must be a non-empty list before compiling")
    legs = plan["legs"]
    home_origins = legs[0]["origins"]  # materialized; resolves a downstream $origins anchor
    has_lodging = "lodging" in plan
    # A trip that never flies home to $origins has no return-departure date to derive a stay from.
    has_stays = has_lodging and (_targets_origins(plan) or "checkout" in plan["lodging"])
    leg_order = {leg["id"]: index for index, leg in enumerate(legs)}
    # Worst-case quota per costed node kind, derived from the trip's effective tuning so a widened
    # page or expansion budget lifts the estimate (agent/derived nodes spend none).
    sweep_quota = (AUTO_WIDEN_CALL_BUDGET_PER_LEG + 1) * tuned(plan, "sweep_page_budget")
    expand_quota = tuned(plan, "expansion_budget_per_endpoint")

    nodes: list[dict] = []
    shortlist_outputs: list[str] = []
    cash_outputs: list[str] = []  # onward.json then bridge.json per cash/either leg, in fold order
    prior_leg: dict | None = None  # the immediately preceding leg
    prior_shortlist: str | None = None  # its shortlist, iff it carried an award lane
    skip_carry: list[dict] = []  # optional-run boundary a successor's sweep also covers (R-A)

    for leg in legs:
        leg_id = leg["id"]
        dests = leg.get("dests")
        is_discover = isinstance(dests, dict)
        if is_discover:
            # Scout proposes the leg's dest airports; its sweep reads them as endpoints at runtime.
            nodes.append(
                _node(
                    f"scout:{leg_id}",
                    "scout",
                    scope="leg",
                    leg=leg_id,
                    inputs=[],
                    outputs=[f"legs/{leg_id}/scout.json"],
                )
            )
        award = leg["mode"] in ("award", "either")
        cash = leg["mode"] in ("cash", "either")
        endpoint_source = _chain_source(leg, prior_leg, prior_shortlist, home_origins)
        if endpoint_source is not None and skip_carry:
            sources = skip_carry
            if dests == ORIGINS_MARKER:
                # The all-predecessors-skipped home union's only consumer is the fly-home-from-home
                # variant, itself R-D excluded — drop it so a $origins leg never sweeps home→home.
                sources = [src for src in skip_carry if src != {"union": list(home_origins)}]
            if sources:
                endpoint_source["skip_sources"] = sources
        # Every source shortlist the leg resolves origins from is a dependency input: the chained
        # ``from`` and each skip source's ``from`` (dedup, order preserved).
        skip_from_inputs = [
            src["from"] for src in skip_carry if "from" in src
        ]
        sweep_inputs: list[str] = []
        for name in (
            [endpoint_source["from"]] if endpoint_source and "from" in endpoint_source else []
        ) + skip_from_inputs:
            if name not in sweep_inputs:
                sweep_inputs.append(name)
        # Cash pairs read a prior shortlist only when the prior leg carried an award lane.
        prior_award = prior_leg is not None and prior_leg["mode"] in ("award", "either")
        origin_shortlist = prior_shortlist if prior_award else None

        leg_sweeps: list[str] = []
        if award:
            for label in _leg_sweep_labels(leg, leg_id):
                spec = leg_id if label is None else f"{leg_id}:{label}"
                leaf = "sweep.json" if label is None else f"sweep-{label}.json"
                output = f"legs/{leg_id}/{leaf}"
                inputs = list(sweep_inputs)
                if is_discover and label is None:  # only the bare sweep reads scout's endpoints
                    inputs.append(f"legs/{leg_id}/scout.json")
                nodes.append(
                    _node(
                        f"sweep:{spec}",
                        "sweep",
                        scope="leg",
                        leg=leg_id,
                        inputs=inputs,
                        outputs=[output],
                        command=["getaway", "sweep", "run", slug, spec],
                        endpoint_source=endpoint_source,
                        quota_cost=sweep_quota,
                    )
                )
                leg_sweeps.append(output)

        if award:
            shortlist = f"legs/{leg_id}/shortlist.json"
            nodes.append(
                _node(
                    f"shortlist:{leg_id}",
                    "shortlist",
                    scope="leg",
                    leg=leg_id,
                    inputs=leg_sweeps,
                    outputs=[shortlist],
                    command=["getaway", "shortlist", "run", slug, "--leg", leg_id],
                )
            )
            shortlist_outputs.append(shortlist)
            prior_shortlist = shortlist
        if cash:
            # Pairs carry the leg's endpoint_source verbatim; _gateway_dates resolves the gateways.
            onward = f"legs/{leg_id}/onward.json"
            bridge = f"legs/{leg_id}/bridge.json"
            nodes.append(
                _node(
                    f"pairs:{leg_id}",
                    "onward",
                    scope="leg",
                    leg=leg_id,
                    inputs=([origin_shortlist] if origin_shortlist else [])
                    + leg_sweeps
                    + [name for name in skip_from_inputs if name != origin_shortlist],
                    outputs=[onward],
                    command=["getaway", "shortlist", "onward", slug, "--leg", leg_id],
                    endpoint_source=endpoint_source,
                )
            )
            nodes.append(
                _node(
                    f"bridge:{leg_id}",
                    "bridge",
                    scope="leg",
                    leg=leg_id,
                    inputs=[onward],
                    outputs=[bridge],
                    command=["getaway", "bridge", slug, "--leg", leg_id],
                )
            )
            cash_outputs += [onward, bridge]

        # An optional leg hands the pre-boundary its successor re-anchors on when skipped; a
        # mandatory leg resets the carry — it can never be skipped past.
        optional = leg.get("optional")
        skip_carry = _skip_contribution(endpoint_source, leg, prior_leg) if optional else []
        prior_leg = leg

    if not shortlist_outputs and not cash_outputs:
        raise UsageError(
            "plan has no retrieval-capable legs to expand: "
            "declare at least one award, cash, or either leg"
        )

    # Optional cash reads: an absent/failed onward or bridge hashes as absent (never blocks
    # award directs) and re-runs expand once priced.
    expand_inputs = [*shortlist_outputs, *cash_outputs]
    # Manual chains declare only when present — a manual-free trip's graph stays byte-identical.
    if _artifact_path(slug, "legs/manual.json").exists():
        expand_inputs.append("legs/manual.json")
    nodes.append(
        _node(
            "expand",
            "expand",
            scope="journey",
            inputs=expand_inputs,
            outputs=["expand.json"],
            command=["getaway", "expand", "run", slug],
            quota_cost=expand_quota,
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
            inputs=[*shortlist_outputs, "expand.json", "assess.json", "enhance-verify.json"],
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
        "trip_type": _shape_label(plan),
        "lodging": has_lodging,
        "requires": ["rooms_session"] if has_stays else [],
        "quota_budget": _quota_budget(nodes, leg_order),
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
