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
from getaway.paths import UsageError, cache_db, emit, map_errors, utcnow
from getaway.store import NoData, connect

DAY_TOKENS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

Row = dict[str, Any]


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


def _leg_intent(plan: dict, leg_id: str) -> dict:
    leg = next((entry for entry in plan["legs"] if entry["id"] == leg_id), None)
    if leg is None:
        raise UsageError(f"unknown shortlist leg: {leg_id!r}")
    return leg


def _sweep_name(leg_id: str, label: str | None) -> str:
    return f"legs/{leg_id}/sweep.json" if label is None else f"legs/{leg_id}/sweep-{label}.json"


def _leg_spec(leg_id: str, trip: dict, prefs_doc: dict) -> dict:
    from getaway.sweeps import derive_specs

    leg = _leg_intent(trip["plan"], leg_id)
    specs = derive_specs(leg)
    labels = [leg_id if s["label"] is None else f"{leg_id}:{s['label']}" for s in specs]
    sweeps = [_sweep_name(leg_id, s["label"]) for s in specs]
    search = [name for name, s in zip(sweeps, specs) if s["kind"] == "search"]
    if leg.get("dests") == trips.ORIGINS_MARKER:  # flying home: partition by origin, home exempt
        field, veto, budget = "origin", set(), RETURN_EXPANSION_BUDGET_PER_ENDPOINT
    else:
        field = "dest"
        veto = set(prefs_doc["avoid_destinations"]) | set(trip["avoid_final_destinations"])
        budget = EXPANSION_BUDGET_PER_ENDPOINT
    # One per-endpoint expansion knob overrides both directions; each branch keeps its own default.
    budget = trip["plan"].get("tuning", {}).get("expansion_budget_per_endpoint", budget)
    return {
        "labels": labels,
        "endpoint_field": field,
        "sweeps": sweeps,
        "search_sweeps": search,
        "veto": veto,
        "budget": budget,
        "name": f"legs/{leg_id}/shortlist.json",
        "label": leg_id,
    }


def shortlist(slug: str, leg: str = "outbound", now: Callable[[], dt.datetime] = utcnow) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    spec = _leg_spec(leg, trip, prefs_doc)
    leg_intent = _leg_intent(plan, leg)
    node_id = f"shortlist:{leg}"
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

    feasible_origins = _expanded_origins(leg_intent.get("origins", [])) | observed
    hard = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "hard"}
    soft = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "soft"}
    sources = set(plan["sources"]) if plan.get("sources") else None
    departure_days = set(prefs_doc["departure_days"])
    chained = spec["endpoint_field"] == "origin"  # a leg flying home draws origins from its chain

    candidates: list[Row] = []
    for row in rows:
        if not row["available"]:
            continue
        if not chained and row["origin"] not in feasible_origins:
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


def _pairs_node(slug: str, leg_id: str) -> dict:
    node_id = f"pairs:{leg_id}"
    node = next((n for n in trips.compile_graph(slug)["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise UsageError(f"no pairs node {node_id!r} in the compiled graph")
    return node


def _onward_dests(leg: dict, home_origins: list[str]) -> list[str]:
    """The concrete landings a cash/either leg forwards to bridge: its declared dests, home when it
    targets ``$origins``, or its bucket landings."""
    dests = leg.get("dests")
    if dests == trips.ORIGINS_MARKER:
        return list(home_origins)
    if isinstance(dests, list):
        return [d for d in dests if isinstance(d, str)]
    landings: list[str] = []
    for bucket in leg.get("buckets", []):
        for dest in bucket["dests"]:
            if dest not in landings:
                landings.append(dest)
    return landings


def _dates_between(start: str, end: str) -> set[str]:
    """Every ISO date in the inclusive ``[start, end]`` span."""
    lo = dt.date.fromisoformat(start)
    span = (dt.date.fromisoformat(end) - lo).days
    return {(lo + dt.timedelta(days=offset)).isoformat() for offset in range(span + 1)}


def _window_dates(trip: dict, leg: dict) -> set[str]:
    """Every date in the leg's own window (its absolute ``window`` or the trip window) — the
    candidate departure dates for a gateway with no observed predecessor arrival: a first-position
    cash leg, or a concrete airport carried forward from a prior cash leg."""
    window = leg["window"] if "window" in leg else trip["window"]
    return _dates_between(window["start"], window["end"])


def _gateway_dates(
    slug: str, node: dict, leg: dict, trip: dict, predecessor: dict | None
) -> dict[str, set[str]]:
    """A cash/either leg's departure gateways keyed to their candidate departure dates.

    Gateways resolve in one fixed order from the compiled pairs node's ``endpoint_source``:

    1. explicit ``override`` origins REPLACE the chain — exactly that list over the leg's own window
       (an open jaw departs where the chain didn't land);
    2. a chained award predecessor (``from``) yields its shortlist's reached endpoints on their
       observed arrival dates (stay-shifted when the predecessor marks a stop), unioned with any
       carried cash-reachable dests (``union``) departing across the leg's own window;
    3. ``union`` alone — a pure-cash/either predecessor with no shortlist rows — departs those dests
       over the leg's window;
    4. no ``endpoint_source`` (a first-position cash leg) departs its own materialized origins over
       that window.

    An empty chained resolution is a data condition raised loud — bridge prices nothing on no
    gateways — mirroring the sweep lane's empty-gateway guard."""
    from getaway.sweeps import _skip_source_window, _stay_shift

    endpoint_source = node["endpoint_source"]
    stay = predecessor.get("stay_nights") if predecessor is not None else None
    dates: dict[str, set[str]] = {}
    if endpoint_source is not None:
        override = endpoint_source.get("override") or {}
        if override.get("origins"):  # open jaw: override REPLACES the chain, departs the leg window
            window = _window_dates(trip, leg)
            return {airport: set(window) for airport in override["origins"]}
        if "from" in endpoint_source:
            prior = json.loads(trips.artifact_read(slug, endpoint_source["from"]))
            for cand in prior["candidates"]:
                dates.setdefault(cand[endpoint_source["field"]], set()).update(
                    _stay_shift({cand["date"]}, stay)
                )
        union = endpoint_source.get("union", [])
        if union:
            window = _window_dates(trip, leg)
            for airport in union:
                dates.setdefault(airport, set()).update(window)
        # Each skip source resolves like the own chain source above: from-arrivals stay-shifted over
        # the SHARED sweep-lane window (_skip_source_window), union dests over the leg's own window.
        for src in endpoint_source.get("skip_sources", []):  # optional-run skip transparency (R-A)
            if "from" in src:
                span = _dates_between(*_skip_source_window(slug, trip, src))
                prior = json.loads(trips.artifact_read(slug, src["from"]))
                for cand in prior["candidates"]:
                    dates.setdefault(cand[src["field"]], set()).update(span)
            for airport in src.get("union", []):
                dates.setdefault(airport, set()).update(_window_dates(trip, leg))
        if not dates:
            source = endpoint_source.get("from") or "carried union"
            raise NoData(f"cash pairs {node['id']!r} in {slug!r} has no gateways: {source} empty")
        return dates
    window = _window_dates(trip, leg)  # first-position cash leg: its own materialized origins
    for airport in leg["origins"]:
        dates.setdefault(airport, set()).update(window)
    return dates


def onward_minima(
    slug: str, leg: str = "outbound", now: Callable[[], dt.datetime] = utcnow
) -> dict:
    from getaway.sweeps import _predecessor

    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    leg_intent = _leg_intent(plan, leg)
    node = _pairs_node(slug, leg)
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, node["id"])
    gateway_dates = _gateway_dates(slug, node, leg_intent, trip, _predecessor(plan, leg))
    onward_dests = _onward_dests(leg_intent, plan["legs"][0]["origins"])
    letter_to_cabin = {letter: name for name, letter in CABIN_PREFIX.items()}
    hard = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "hard"}

    minima: dict[tuple[str, str, str, str], Row] = {}
    if leg_intent["mode"] != "cash":  # an award/either leg's own availability is the award option
        labels = _leg_spec(leg, trip, prefs_doc)["labels"]
        store = connect(cache_db(), now=now)
        for row in store.query_availability(trip_slug=slug, labels=labels):
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

    # Cross the reached gateways with the leg's onward dests on each gateway's arrival dates: the
    # cash lane prices every reachable pair, never gated on award availability (which double-gates).
    pair_keys = sorted(
        {
            (gateway, dest, date)
            for gateway, dates in gateway_dates.items()
            for dest in onward_dests
            for date in dates
        }
    )
    bridge_pairs = [{"gateway": g, "onward_dest": d, "date": date} for g, d, date in pair_keys]
    doc = {"minima": list(minima.values()), "bridge_pairs": bridge_pairs}
    trips.artifact_write(slug, node["outputs"][0], json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, node["id"], inputs_fp=inputs_fp, now=now)
    return doc


shortlist_group = click.Group("shortlist", help="Select leg candidates from swept availability.")


@shortlist_group.command("run")
@click.argument("slug")
@click.option("--leg", default="outbound")
@map_errors
def _run_cmd(slug: str, leg: str) -> None:
    emit(shortlist(slug, leg=leg))


@shortlist_group.command("onward")
@click.argument("slug")
@click.option("--leg", required=True)
@map_errors
def _onward_cmd(slug: str, leg: str) -> None:
    from getaway.constants import EXIT_NO_DATA

    try:
        emit(onward_minima(slug, leg))
    except NoData as err:  # empty chained gateways: no pairs to price, exit for walker backoff
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_NO_DATA) from err
