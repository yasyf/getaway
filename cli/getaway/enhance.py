"""Background enhancers — low-priority verification of uncertain availability rows.

An enhancer is fire-and-forget: it never blocks the walk or the board, its failure is silence,
and results land only through ``enhance merge`` — a flock-guarded upsert — never a whole-file
``trip artifact write`` that would clobber under concurrency. ``enhance targets`` computes the
worklist over the trip's cached artifacts; ``enhance merge`` folds background results into
``enhance-<name>.json``. The first enhancer is ``verify``: live-site checks on award rows the
trip is doubling down on. Rank re-derives its join keys from the legs, so nothing here stores a
target list the rank fold could drift from.
"""

import datetime as dt
import json
from collections import Counter

import click

from getaway import registry, trips
from getaway.constants import NODE_TTL_HOURS
from getaway.paths import (
    StateConflictError,
    UsageError,
    atomic_update,
    emit,
    map_errors,
    require_keys,
    require_str,
)

ENHANCERS = ("verify",)
OUTCOMES = frozenset({"confirmed", "gone", "degraded", "inconclusive"})
METHODS = frozenset({"public", "cookie"})
STALE_HOURS = NODE_TTL_HOURS["expand"]


def _artifact_leaf(name: str) -> str:
    return f"enhance-{name}.json"


def _require_enhancer(name: str) -> str:
    if name not in ENHANCERS:
        raise UsageError(f"unknown enhancer {name!r}; known: {list(ENHANCERS)}")
    return name


def present(slug: str, name: str) -> bool:
    return _artifact_leaf(name) in trips.artifact_list(slug)


def results_index(slug: str, name: str) -> dict:
    leaf = _artifact_leaf(name)
    if leaf not in trips.artifact_list(slug):
        return {}
    return json.loads(trips.artifact_read(slug, leaf))["results"]


def _require_checked_at(value: object, label: str) -> None:
    value = require_str(value, label)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as err:
        raise UsageError(f"{label} {value!r} must be an ISO 8601 timestamp") from err
    if parsed.tzinfo is None:
        raise UsageError(f"{label} {value!r} must be timezone-aware")


def _checked_at(row: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(row["checked_at"])


def _validate_result_row(row: object, label: str) -> dict:
    row = require_keys(
        row, {"target_id", "outcome", "checked_at", "method", "observed", "evidence"}, label
    )
    require_str(row["target_id"], f"{label}.target_id")
    require_str(row["outcome"], f"{label}.outcome")
    if row["outcome"] not in OUTCOMES:
        raise UsageError(f"{label}.outcome {row['outcome']!r} must be one of {sorted(OUTCOMES)}")
    _require_checked_at(row["checked_at"], f"{label}.checked_at")
    require_str(row["method"], f"{label}.method")
    if row["method"] not in METHODS:
        raise UsageError(f"{label}.method {row['method']!r} must be one of {sorted(METHODS)}")
    observed = row["observed"]
    if row["outcome"] in ("confirmed", "degraded"):
        if not isinstance(observed, dict):
            raise UsageError(f"{label}.observed must be an object for {row['outcome']}")
    elif observed is not None:
        raise UsageError(f"{label}.observed must be null for {row['outcome']}")
    require_str(row["evidence"], f"{label}.evidence")
    return row


def validate_enhancer_doc(doc: object, name: str) -> None:
    doc = require_keys(doc, {"enhancer", "results"}, name)
    enhancer = doc["enhancer"]
    require_str(enhancer, f"{name}.enhancer")
    if enhancer not in ENHANCERS:
        raise UsageError(f"{name}.enhancer {enhancer!r} must be one of {list(ENHANCERS)}")
    if enhancer != name:
        raise UsageError(f"{name}.enhancer {enhancer!r} does not match filename enhancer {name!r}")
    results = doc["results"]
    if not isinstance(results, dict):
        raise UsageError(f"{name}.results must be an object")
    for target_id, row in results.items():
        label = f"{name}.results[{target_id!r}]"
        _validate_result_row(row, label)
        if row["target_id"] != target_id:
            raise UsageError(
                f"{label}.target_id {row['target_id']!r} does not match key {target_id!r}"
            )


def _program_join(program: str, programs: dict) -> tuple[list[str], str]:
    row = programs.get(program)
    if row is None:
        raise UsageError(f"unknown program {program!r}")
    return row["domains"], row["gather_auth"]


def _primary_site(booking_links: list[dict]) -> str | None:
    return next((link["link"] for link in booking_links if link["primary"]), None)


def _leg_reason(fact: dict) -> str | None:
    if fact["seat_sufficiency"]["state"] == "unknown":
        return "seats_unknown"
    age = fact["cache_age_hours"]
    if age is not None and age > STALE_HOURS:
        return "stale_cache"
    return None


def _award_target(leg: dict, fact: dict, reason: str, party: int, programs: dict) -> dict:
    hosts, gather_auth = _program_join(leg["source"], programs)
    detail = leg["detail"]
    booking_links = detail["booking_links"]
    return {
        "target_id": f"{leg['id']}:{leg['cabin']}",
        "kind": "award_leg",
        "reason": reason,
        "availability_id": leg["id"],
        "program": leg["source"],
        "hosts": hosts,
        "gather_auth": gather_auth,
        "origin": fact["origin"],
        "dest": fact["dest"],
        "date": fact["departs_local"][:10],
        "cabin": leg["cabin"],
        "airlines": leg["airlines"],
        "party": party,
        "miles": detail["mileage"],
        "remaining_seats": detail["remaining_seats"],
        "cache_age_hours": fact["cache_age_hours"],
        "booking_links": booking_links,
        "site": _primary_site(booking_links),
        "journeys": [],
    }


def _award_targets(journeys: list[dict], party: int, programs: dict) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for journey in journeys:
        jid = journey["id"]
        for leg, fact in zip(journey["legs"], journey["fit_facts"]["legs"], strict=True):
            if leg.get("mode") == "cash":
                continue
            reason = _leg_reason(fact)
            if reason is None:
                continue
            key = (leg["id"], leg["cabin"])
            target = by_key.get(key)
            if target is None:
                target = _award_target(leg, fact, reason, party, programs)
                by_key[key] = target
            target["journeys"].append(jid)
    return list(by_key.values())


def _lead_target(lead: dict, party: int, window: dict, programs: dict) -> dict:
    ob = lead["outbound"]
    hosts, gather_auth = _program_join(ob["source"], programs)
    return {
        "target_id": f"lead:{ob['dest']}:{ob['cabin']}",
        "kind": "empty_lead",
        "reason": "searched_empty_unverified",
        "program": ob["source"],
        "hosts": hosts,
        "gather_auth": gather_auth,
        "origin": ob["dest"],
        "dest": ob["detail"]["segments"][0]["origin"],
        "date": window["end"],
        "cabin": ob["cabin"],
        "party": party,
        "return_search_state": lead["return_search_state"],
        "searched_at": lead["searched_at"],
        "cache_age_hours": lead["cache_age_hours"],
    }


def _lead_targets(leads: list[dict], party: int, window: dict, programs: dict) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for lead in leads:
        state = lead["return_search_state"]
        if state.get("state") != "searched_empty" or state.get("verification") != "unverified":
            continue
        ob = lead["outbound"]
        key = (ob["dest"], ob["cabin"])
        if key not in by_key:
            by_key[key] = _lead_target(lead, party, window, programs)
    return list(by_key.values())


def targets(slug: str, name: str) -> list[dict]:
    _require_enhancer(name)
    if "expand.json" not in trips.artifact_list(slug):
        raise StateConflictError(f"{slug} has no expand.json; run expand before enhancing")
    expand = json.loads(trips.artifact_read(slug, "expand.json"))
    if "finalists.json" in trips.artifact_list(slug):
        finalists = json.loads(trips.artifact_read(slug, "finalists.json"))
        journeys = [entry["journey"] for entry in finalists["journeys"]]
    else:
        journeys = expand["journeys"]
    trip = trips.show(slug)
    party = trip["party"]
    programs = registry.programs()
    rows = _award_targets(journeys, party, programs)
    rows += _lead_targets(expand["unpaired_outbounds"], party, trip["window"], programs)
    rows.sort(key=lambda target: target["target_id"])
    return rows


def _upsert(results: dict, row: dict) -> None:
    prior = results.get(row["target_id"])
    if prior is None or _checked_at(row) > _checked_at(prior):
        results[row["target_id"]] = row


def merge(slug: str, name: str, rows: object) -> dict:
    _require_enhancer(name)
    if not isinstance(rows, list):
        raise UsageError("enhance merge expects a JSON array on stdin")
    validated = [
        _validate_result_row(row, f"enhance merge input[{i}]") for i, row in enumerate(rows)
    ]

    def mutate(current: dict) -> dict:
        doc: dict = current if current else {"enhancer": name, "results": {}}
        for row in validated:
            _upsert(doc["results"], row)
        validate_enhancer_doc(doc, name)
        return doc

    return atomic_update(trips._artifact_path(slug, _artifact_leaf(name)), mutate)


def _resume_line(doc: dict) -> str:
    results = list(doc["results"].values())
    counts = Counter(row["outcome"] for row in results)
    tally = ", ".join(f"{counts[outcome]} {outcome}" for outcome in sorted(counts))
    if not results:
        return f"Enhancers: {doc['enhancer']} — 0 results"
    latest = max(results, key=_checked_at)["checked_at"]
    body = f"{len(results)} results ({tally}), latest {latest[11:16]}"
    return f"Enhancers: {doc['enhancer']} — {body}"


def resume_lines(slug: str) -> list[str]:
    lines = []
    for leaf in trips.artifact_list(slug):
        if leaf.startswith("enhance-") and leaf.endswith(".json"):
            lines.append(_resume_line(json.loads(trips.artifact_read(slug, leaf))))
    return lines


enhance_group = click.Group(
    "enhance", help="Background enhancers over uncertain availability rows."
)


@enhance_group.command("targets")
@click.argument("slug")
@click.argument("name")
@map_errors
def _targets_cmd(slug: str, name: str) -> None:
    emit({"targets": targets(slug, name)})


@enhance_group.command("merge")
@click.argument("slug")
@click.argument("name")
@map_errors
def _merge_cmd(slug: str, name: str) -> None:
    try:
        rows = json.loads(click.get_text_stream("stdin").read())
    except json.JSONDecodeError as err:
        raise UsageError(f"invalid JSON on stdin: {err}") from err
    emit(merge(slug, name, rows))
