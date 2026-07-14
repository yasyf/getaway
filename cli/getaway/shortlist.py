import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import prefs, registry, trips
from getaway.constants import (
    CABIN_PREFIX,
    EXPANSION_BUDGET_PER_ENDPOINT,
    RETURN_EXPANSION_BUDGET_PER_ENDPOINT,
)
from getaway.paths import cache_db, emit, map_errors, utcnow
from getaway.store import connect

DAY_TOKENS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

Row = dict[str, Any]

_NODE_IDS = {
    "legs/outbound/shortlist.json": "shortlist:outbound",
    "legs/outbound/shortlist-gateway.json": "shortlist:outbound:gateway",
    "legs/return/shortlist.json": "shortlist:return",
}


def _weekday_token(date_str: str) -> str:
    return DAY_TOKENS[dt.date.fromisoformat(date_str).weekday()]


def _airlines(row: Row) -> list[str]:
    return [a for a in row["airlines"].split(", ") if a]


def _expanded_origins(tokens: list[str]) -> set[str]:
    """Planned origins expanded to concrete airports; a region with no local airport list (or an
    undocumented pseudo-code) stays a literal — the sweep-observed set covers its expansion."""
    result = set(tokens)
    for token in tokens:
        try:
            result.update(registry.expand_region(token))
        except registry.NoData:
            pass
    return result


def _read_sweep(slug: str, name: str) -> dict | None:
    if name in trips.artifact_list(slug):
        return json.loads(trips.artifact_read(slug, name))
    return None


def _group_best(candidates: list[Row]) -> list[Row]:
    best: dict[tuple[str, str, str, str, str], Row] = {}
    for cand in candidates:
        key = (cand["origin"], cand["dest"], cand["date"], cand["source"], cand["cabin"])
        current = best.get(key)
        if current is None or cand["mileage"] < current["mileage"]:
            best[key] = cand
    return list(best.values())


def _cohort_select(cands: list[Row], budget: int) -> list[Row]:
    """Round-robin across (date, source) cohorts, cheapest first — one hot date or program can't
    fill the per-endpoint budget on its own."""
    cohorts: dict[tuple[str, str], list[Row]] = {}
    for cand in sorted(cands, key=lambda c: c["mileage"]):
        cohorts.setdefault((cand["date"], cand["source"]), []).append(cand)
    queues = list(cohorts.values())
    selected: list[Row] = []
    while len(selected) < budget:
        progressed = False
        for queue in queues:
            if queue and len(selected) < budget:
                selected.append(queue.pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def _leg_spec(leg: str, gateway: bool, trip: dict, prefs_doc: dict) -> dict:
    if leg == "return":
        return {
            "labels": ["return"],
            "endpoint_field": "origin",
            "sweeps": ["legs/return/sweep.json"],
            "search_sweeps": ["legs/return/sweep.json"],
            "veto": set(),  # return destinations are home, exempt from the veto
            "budget": RETURN_EXPANSION_BUDGET_PER_ENDPOINT,
            "name": "legs/return/shortlist.json",
            "label": "return",
        }
    if gateway:
        return {
            "labels": ["outbound:gateways"],
            "endpoint_field": "dest",
            "sweeps": ["legs/outbound/sweep-gateways.json"],
            "search_sweeps": ["legs/outbound/sweep-gateways.json"],
            "veto": set(),  # gateways are waypoints, not final destinations
            "budget": EXPANSION_BUDGET_PER_ENDPOINT,
            "name": "legs/outbound/shortlist-gateway.json",
            "label": "outbound:gateway",
        }
    from getaway.sweeps import derive_specs

    specs = [s for s in derive_specs(trip, prefs_doc) if s["label"] != "gateways"]
    return {
        "labels": [f"outbound:{s['label']}" for s in specs],
        "endpoint_field": "dest",
        "sweeps": [f"legs/outbound/sweep-{s['label']}.json" for s in specs],
        "search_sweeps": [
            f"legs/outbound/sweep-{s['label']}.json" for s in specs if s["kind"] == "search"
        ],
        "veto": set(prefs_doc["avoid_destinations"]) | set(trip["avoid_final_destinations"]),
        "budget": EXPANSION_BUDGET_PER_ENDPOINT,
        "name": "legs/outbound/shortlist.json",
        "label": "outbound",
    }


def shortlist(
    slug: str, leg: str = "outbound", gateway: bool = False, now: Callable[[], dt.datetime] = utcnow
) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    spec = _leg_spec(leg, gateway, trip, prefs_doc)
    node_id = _NODE_IDS[spec["name"]]
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, node_id)

    search_states: dict = {}
    observed: set[str] = set()
    superseded_rows = 0
    for name in spec["sweeps"]:
        swept = _read_sweep(slug, name)
        if swept is not None:
            search_states.update(swept["search_states"])
            provenance = swept["provenance"]
            if "superseded_rows" in provenance:
                superseded_rows += provenance["superseded_rows"]["count"]
            if name in spec["search_sweeps"]:
                observed.update(provenance["expanded_origins"])

    store = connect(cache_db(), now=now)
    rows = store.query_availability(trip_slug=slug, labels=spec["labels"])
    considered = len({row["id"] for row in rows})

    feasible_origins = _expanded_origins(plan["origins"]) | observed
    hard = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "hard"}
    soft = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "soft"}
    sources = set(plan["sources"]) if plan.get("sources") else None
    departure_days = set(prefs_doc["departure_days"])

    candidates: list[Row] = []
    for row in rows:
        if not row["available"]:
            continue
        if leg != "return" and row["origin"] not in feasible_origins:
            continue  # feasibility: departs a planned (server-expanded) origin
        if sources is not None and row["source"] not in sources:
            continue
        if row["dest"] in spec["veto"]:
            continue
        airlines = _airlines(row)
        if airlines and all(a in hard for a in airlines):
            continue
        candidates.append(
            {
                "id": row["id"],
                "cabin": row["cabin"],
                "date": row["date"],
                "origin": row["origin"],
                "dest": row["dest"],
                "source": row["source"],
                "mileage": row["mileage_cost"],
                "seats": row["remaining_seats"],
                "airlines": row["airlines"],
                "direct": row["direct"],
                "soft": any(a in soft for a in airlines),
                "departure_day_match": bool(departure_days)
                and _weekday_token(row["date"]) in departure_days,
            }
        )

    field = spec["endpoint_field"]
    if field == "origin":
        floor_origins: set[str] = set()  # Return endpoints are already partitioned by origin.
    else:
        floor_origins = (
            {prefs_doc["home_airport"]} if prefs_doc["home_airport"] is not None else set()
        ) & feasible_origins

    by_endpoint: dict[str, list[Row]] = {}
    for cand in _group_best(candidates):
        by_endpoint.setdefault(cand[field], []).append(cand)

    kept: list[Row] = []
    truncation: dict = {}
    for endpoint, cands in by_endpoint.items():
        floor_picks: dict[str, Row] = {}
        for cand in sorted(cands, key=lambda c: c["mileage"]):
            if cand["origin"] in floor_origins:
                floor_picks.setdefault(cand["origin"], cand)

        round_robin = _cohort_select(cands, spec["budget"])
        selection_pool = list(floor_picks.values())
        selection_pool.extend(cand for cand in round_robin if cand not in selection_pool)
        selected = selection_pool[: spec["budget"]]
        displaced = sum(cand not in selected for cand in round_robin)
        kept.extend(selected)
        if len(cands) > len(selected):
            truncation[endpoint] = {"considered": len(cands), "kept": len(selected)}
            if displaced:
                truncation[endpoint]["displaced"] = displaced
    kept.sort(key=lambda c: (int(c["soft"]), c["mileage"], 0 if c["departure_day_match"] else 1))

    doc = {
        "candidates": kept,
        "considered": considered,
        "search_states": search_states,
        "leg": spec["label"],
        "truncation": truncation,
    }
    if superseded_rows:
        doc["provenance"] = {"superseded_rows": {"count": superseded_rows}}
    trips.artifact_write(slug, spec["name"], json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, node_id, inputs_fp=inputs_fp, now=now)
    return doc


def onward_minima(slug: str, now: Callable[[], dt.datetime] = utcnow) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    hybrid = plan["hybrid"]
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, "onward")
    gateway_doc = json.loads(trips.artifact_read(slug, "legs/outbound/shortlist-gateway.json"))
    gateway_dates: dict[str, set[str]] = {}
    for cand in gateway_doc["candidates"]:
        gateway_dates.setdefault(cand["dest"], set()).add(cand["date"])
    onward_dests = hybrid["onward_dests"]
    letter_to_cabin = {letter: name for name, letter in CABIN_PREFIX.items()}
    hard = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "hard"}

    store = connect(cache_db(), now=now)
    rows = store.query_availability(trip_slug=slug, labels=["outbound:onward"])
    minima: dict[tuple[str, str, str, str], Row] = {}
    for row in rows:
        if not row["available"]:
            continue
        if row["origin"] not in gateway_dates or row["dest"] not in onward_dests:
            continue
        if row["date"] < min(gateway_dates[row["origin"]]):
            continue  # structural: departs before the earliest feasible gateway arrival
        airlines = _airlines(row)
        if airlines and all(a in hard for a in airlines):
            continue
        cabin = letter_to_cabin[row["cabin"]]
        key = (row["origin"], row["dest"], cabin, row["date"])
        current = minima.get(key)
        if current is None or row["mileage_cost"] < current["mileage"]:
            minima[key] = {
                "gateway": row["origin"],
                "onward_dest": row["dest"],
                "cabin": cabin,
                "id": row["id"],
                "date": row["date"],
                "source": row["source"],
                "mileage": row["mileage_cost"],
                "seats": row["remaining_seats"],
                "airlines": row["airlines"],
                "direct": row["direct"],
            }

    pair_dates = sorted({(m["gateway"], m["onward_dest"], m["date"]) for m in minima.values()})
    bridge_pairs = [{"gateway": g, "onward_dest": d, "date": date} for g, d, date in pair_dates]
    doc = {"minima": list(minima.values()), "bridge_pairs": bridge_pairs}
    trips.artifact_write(slug, "legs/outbound/onward.json", json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, "onward", inputs_fp=inputs_fp, now=now)
    return doc


shortlist_group = click.Group("shortlist", help="Select leg candidates from swept availability.")


@shortlist_group.command("run")
@click.argument("slug")
@click.option("--leg", default="outbound", type=click.Choice(["outbound", "return"]))
@click.option("--gateway", is_flag=True)
@map_errors
def _run_cmd(slug: str, leg: str, gateway: bool) -> None:
    emit(shortlist(slug, leg=leg, gateway=gateway))


@shortlist_group.command("onward")
@click.argument("slug")
@map_errors
def _onward_cmd(slug: str) -> None:
    emit(onward_minima(slug))
