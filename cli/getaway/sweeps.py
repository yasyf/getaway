import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click
import httpx

from getaway import prefs, registry, trips
from getaway.constants import (
    AUTO_WIDEN_CALL_BUDGET_PER_LEG,
    DATE_WIDEN_STEP_DAYS,
    DEFAULT_QUOTA_FLOOR,
    GENERATION_CUTTING_COMPLETENESS,
    SEARCH_PAGE_SIZE,
    SOFT_DATE_SEARCH_PADDING_DAYS,
    SWEEP_PAGE_BUDGET,
)
from getaway.paths import UsageError, cache_db, emit, map_errors, utcnow
from getaway.seats import AuthError, PaginationError, SeatsClient
from getaway.store import NoData, QuotaFloorError, availability_scope, connect, search_scope

Spec = dict[str, Any]
Leg = dict[str, Any]


def _region_slug(region: str) -> str:
    return region.lower().replace(" ", "-")


def _shift_date(date_str: str, days: int) -> str:
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=days)).isoformat()


def _predecessor(plan: dict, leg_id: str) -> Leg | None:
    """The immediately-preceding non-discover leg — the one this leg chains from. A discover leg
    breaks the chain (its successor declares explicit origins), so the leg right after one, like the
    first leg, has no predecessor. Mirrors the fold's ``prior_leg`` in ``trips.compile_graph``."""
    legs = plan["legs"]
    index = next(i for i, entry in enumerate(legs) if entry["id"] == leg_id)
    if index == 0:
        return None
    prior = legs[index - 1]
    return None if isinstance(prior.get("dests"), dict) else prior


def _stay_shift(dates: set[str], stay: dict | None) -> set[str]:
    """A stay-marked boundary pushes each predecessor-arrival date to the departures that honor the
    declared stay: ``[arrival+min .. arrival+max]``. No stay marker leaves the date a same-day
    connection, unshifted (the degenerate case)."""
    if stay is None:
        return set(dates)
    return {
        _shift_date(date, nights)
        for date in dates
        for nights in range(stay["min"], stay["max"] + 1)
    }


def derive_specs(leg: Leg) -> list[Spec]:
    """An award/either leg's sweep specs: one search per bucket, one bulk-availability per program
    sweep, else one bare search (label ``None``) over the leg's own dests. Region tokens and
    endpoints stay as written — the server expands them and the leg records the concrete set at
    sweep time. Labels mirror ``trips._leg_sweep_labels`` so node ids and store labels agree.
    """
    specs: list[Spec] = []
    for bucket in leg.get("buckets", []):
        specs.append({"label": bucket["name"], "kind": "search", "dests": bucket["dests"]})
    for sweep in leg.get("program_sweeps", []):
        # origin_region takes a "from-" infix so a source's dest and origin sweeps over one
        # continent stay distinct; the slug is total over the closed continent vocabulary.
        if "dest_region" in sweep:
            label = f"{sweep['source']}-{_region_slug(sweep['dest_region'])}"
        else:
            label = f"{sweep['source']}-from-{_region_slug(sweep['origin_region'])}"
        spec: Spec = {"label": label, "kind": "availability", "source": sweep["source"]}
        if "dest_region" in sweep:
            spec["dest_region"] = sweep["dest_region"]
        if "origin_region" in sweep:
            spec["origin_region"] = sweep["origin_region"]
        specs.append(spec)
    return specs or [{"label": None, "kind": "search", "dests": None}]


def _leg_window(
    trip: dict,
    leg: Leg,
    is_first: bool,
    is_return: bool,
    predecessor: Leg | None,
    arrivals: dict[str, set[str]],
) -> tuple[str, str]:
    """A leg's sweep window: an absolute per-intent ``window`` wins; a middle leg chained past a
    stay-marked stop departs within ``[arrival+min .. arrival+max]`` of its predecessor's observed
    arrivals (the only dates that honor the stay); otherwise derive from the trip window,
    positionally anchored — the first leg carries the departure-side constraint, a leg flying to
    ``$origins`` the return-side one, and any other middle leg the padded trip window."""
    if "window" in leg:
        return leg["window"]["start"], leg["window"]["end"]
    if not is_first and not is_return and predecessor is not None and "stay_nights" in predecessor:
        observed = {date for dates in arrivals.values() for date in dates}
        if observed:
            shifted = _stay_shift(observed, predecessor["stay_nights"])
            return min(shifted), max(shifted)
    window = trip["window"]
    constraints = trip["plan"].get("constraints", {})
    pad = SOFT_DATE_SEARCH_PADDING_DAYS
    if is_return:
        hard = constraints.get("return_arrival_by")
        if hard:
            return window["start"], hard["latest_local_date"]
        return window["start"], _shift_date(window["end"], pad)
    if is_first:
        hard = constraints.get("outbound_departure_window")
        if hard:
            return hard["start"], hard["end"]
    return _shift_date(window["start"], -pad), _shift_date(window["end"], pad)


def _chained_endpoints(
    slug: str, key: str, endpoint_source: dict
) -> tuple[list[str], dict[str, set[str]]]:
    """A chained leg's resolved origins and its predecessor's arrival dates per reached endpoint.

    Origins come from the ``from`` shortlist's ``field`` values (award lane) unioned with the
    carried cash-reachable dests, or the explicit override; the arrival dates ride along for the
    stay-shifted window derivation. An empty resolution — an empty predecessor shortlist and empty
    union with no override — leaves the leg with nowhere to depart, a data condition raised loud
    with zero HTTP (walker backoff), generalizing HEAD's empty-gateway guard to any chained leg.
    """
    override = endpoint_source.get("override") or {}
    arrivals: dict[str, set[str]] = {}
    if "from" in endpoint_source:
        prior = json.loads(trips.artifact_read(slug, endpoint_source["from"]))
        for cand in prior["candidates"]:
            arrivals.setdefault(cand[endpoint_source["field"]], set()).add(cand["date"])
    if override.get("origins"):
        origins = list(override["origins"])
    else:
        origins = sorted(set(arrivals) | set(endpoint_source.get("union", [])))
    if not origins:
        source = endpoint_source.get("from") or "carried union"
        raise NoData(f"chained sweep {key!r} for {slug!r} has no gateways: {source} is empty")
    return origins, arrivals


def _leg_for_key(slug: str, key: str, trip: dict, endpoint_source: dict | None) -> Leg:
    """The sweep leg for a compiled ``sweep:<leg-id>[:<label>]`` node: origins from the leg's own
    (first leg) or its chain (``endpoint_source``); dests from the label's bucket, the leg's own
    concrete dests, or home when the leg targets ``$origins``."""
    legs = trip["plan"]["legs"]
    leg_id, _, label = key.partition(":")
    leg = next((entry for entry in legs if entry["id"] == leg_id), None)
    if leg is None:
        raise UsageError(f"unknown sweep leg: {leg_id!r}")
    is_first = legs.index(leg) == 0
    is_return = leg.get("dests") == trips.ORIGINS_MARKER
    predecessor = _predecessor(trip["plan"], leg_id)
    spec = next((s for s in derive_specs(leg) if s["label"] == (label or None)), None)
    if spec is None:
        raise UsageError(f"no sweep spec for leg {leg_id!r} label {(label or None)!r}")
    if spec["kind"] == "availability":
        # A region program sweep queries by continent, not concrete origins — the chain is inert
        # here, so its empty-gateway guard (search-only) does not apply.
        return {
            "id": f"sweep:{key}",
            "kind": "availability",
            "path": "/availability",
            "source": spec["source"],
            "origin_region": spec.get("origin_region"),
            "dest_region": spec.get("dest_region"),
            "endpoints": [label],
            "endpoint_field": None,
            "window": _leg_window(trip, leg, is_first, is_return, predecessor, {}),
        }
    if endpoint_source is None:
        origins, arrivals = leg["origins"], {}
    else:
        origins, arrivals = _chained_endpoints(slug, key, endpoint_source)
    window = _leg_window(trip, leg, is_first, is_return, predecessor, arrivals)
    bare_home = spec["dests"] is None and is_return
    if spec["dests"] is not None:
        dests = spec["dests"]
    elif bare_home:
        dests = legs[0]["origins"]  # home, resolving the leg's "$origins" dests
    else:
        dests = leg["dests"]
    endpoints, field = (origins, "origin") if bare_home else (dests, "dest")
    return {
        "id": f"sweep:{key}",
        "kind": "search",
        "path": "/search",
        "origins": origins,
        "dests": dests,
        "endpoints": endpoints,
        "endpoint_field": field,
        "window": window,
        "source": None,
    }


def _api_params(leg: Leg, start: str, end: str, plan: dict) -> dict:
    # All cabins ride one call (cabin is a preference); include_filtered defeats the server's
    # dynamic-price hiding so expensive near-misses stay visible.
    params: dict[str, Any] = {
        "start_date": start,
        "end_date": end,
        "take": SEARCH_PAGE_SIZE,
        "include_filtered": "true",
    }
    if leg["kind"] == "search":
        params["origin_airport"] = ",".join(leg["origins"])
        params["destination_airport"] = ",".join(leg["dests"])
        if plan.get("sources"):
            params["sources"] = ",".join(plan["sources"])
    else:
        params["source"] = leg["source"]
        if leg.get("origin_region"):
            params["origin_region"] = leg["origin_region"]
        if leg.get("dest_region"):
            params["destination_region"] = leg["dest_region"]
    return params


def _row_endpoint(row: dict, field: str) -> str:
    return row["Route"]["OriginAirport" if field == "origin" else "DestinationAirport"]


def _is_region(code: str) -> bool:
    try:
        registry.region(code)
    except registry.NoData:
        return False
    return True


def _scope(
    leg: Leg, rows: list[dict], searched: list[dict], sources: list[str] | None
) -> list[dict]:
    """The constraint groups a prior row must satisfy to fall under this sweep's supersede, so a
    run never supersedes what it did not search. A bulk-availability leg scopes on its one program
    source and the searched region columns (both directions when both are set); a search leg on the
    concrete origin×dest airports it actually reached (region tokens resolved to demonstrated
    airports), plus each plan source when the search is source-restricted."""
    if leg["endpoint_field"] is None:  # bulk availability: one program source, region columns
        return availability_scope(
            leg["source"], leg.get("origin_region"), leg.get("dest_region"), searched
        )
    return search_scope(leg["origins"], leg["dests"], rows, searched, sources=sources)


def _search_states(leg: Leg, rows: list[dict], has_more: bool, stop: tuple | None) -> dict:
    endpoints = leg["endpoints"]
    if stop is not None:
        if len(stop) == 2:
            return {e: {"state": stop[0], "reason": stop[1]} for e in endpoints}
        return {
            e: {"state": stop[0], "reason": stop[1], "retryability": stop[2]}
            for e in endpoints
        }
    if has_more:  # a truncated page: an absent endpoint is unknown, not empty
        return {
            e: {"state": "partial", "reason": "page_budget", "has_more": True} for e in endpoints
        }
    field = leg["endpoint_field"]
    if field is None:  # region-level availability sweep: one endpoint
        return {e: {"state": "complete" if rows else "searched_empty"} for e in endpoints}
    seen = {_row_endpoint(row, field) for row in rows}
    states: dict = {}
    for e in endpoints:
        found = e in seen or (bool(rows) and _is_region(e))
        states[e] = {"state": "complete" if found else "searched_empty"}
    return states


def _completeness(states: dict) -> str:
    kinds = {spec["state"] for spec in states.values()}
    for level in ("failed", "not_run", "partial", "complete"):
        if level in kinds:
            return level
    return "searched_empty"


def _sweep_leg(client: SeatsClient, leg: Leg, plan: dict) -> dict:
    start, end = leg["window"]
    rows: list[dict] = []
    seen_ids: set[str] = set()
    searched: list[dict] = []
    has_more = False
    stop: tuple | None = None
    widen = 0
    calls = 0
    covered = False  # a page returned data (not merely a completed call): the bar for 'partial'
    while True:
        try:
            params = _api_params(leg, start, end, plan)
            if leg["kind"] == "search":
                params["order_by"] = "lowest_mileage"
                page = client._paginate(
                    leg["path"], params, SEARCH_PAGE_SIZE, SWEEP_PAGE_BUDGET
                )
                page_rows = page.rows
                has_more = page.has_more
                calls += page.calls
            else:
                payload = client._get(leg["path"], params)
                page_rows = payload["data"]
                has_more = bool(payload.get("hasMore"))
                calls += 1
        except PaginationError as err:
            calls += err.calls
            for row in err.rows:
                if row["ID"] not in seen_ids:
                    seen_ids.add(row["ID"])
                    rows.append(row)
            if err.covered:  # a page returned this window: it was genuinely searched
                searched.append({"start": start, "end": end})
                covered = True
            if covered:  # a page landed this run (this window or an earlier widen): partial
                if isinstance(err.cause, QuotaFloorError):
                    stop = ("partial", "quota_budget")
                else:
                    stop = ("partial", str(err.cause), "retryable")
            elif isinstance(err.cause, QuotaFloorError):
                stop = ("not_run", "quota_budget")
            else:
                stop = ("failed", str(err.cause), "retryable")
            break
        except QuotaFloorError:  # pre-request refusal: this call never left the client
            stop = ("partial", "quota_budget") if covered else ("not_run", "quota_budget")
            break
        except httpx.HTTPError as err:
            if isinstance(err, httpx.HTTPStatusError):
                calls += 1  # a returned error response is a completed call
            if covered:
                stop = ("partial", str(err), "retryable")
            else:
                stop = ("failed", str(err), "retryable")
            break
        searched.append({"start": start, "end": end})
        covered = True
        for row in page_rows:
            if row["ID"] not in seen_ids:
                seen_ids.add(row["ID"])
                rows.append(row)
        if rows or widen >= AUTO_WIDEN_CALL_BUDGET_PER_LEG:
            break
        widen += 1
        start = _shift_date(start, -DATE_WIDEN_STEP_DAYS)
        end = _shift_date(end, DATE_WIDEN_STEP_DAYS)
    states = _search_states(leg, rows, has_more, stop)
    return {
        "rows": rows,
        "search_states": states,
        "searched": searched,
        "expanded_origins": sorted({row["Route"]["OriginAirport"] for row in rows}),
        "completeness": _completeness(states),
        "calls": calls,
    }


def _sweep_node(slug: str, key: str) -> dict:
    node_id = f"sweep:{key}"
    node = next((n for n in trips.compile_graph(slug)["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise UsageError(f"no sweep node {node_id!r} in the compiled graph")
    return node


def run(
    slug: str,
    key: str,
    refresh: bool = False,
    quota_floor: int = DEFAULT_QUOTA_FLOOR,
    now: Callable[[], dt.datetime] = utcnow,
) -> dict:
    trip = trips.show(slug)
    node = _sweep_node(slug, key)  # endpoint_source + output name are compiled once, offline
    node_id = node["id"]
    name = node["outputs"][0]
    if not refresh:  # freshness self-skip checks before reserving quota
        fresh, _ = trips.phase_check(slug, node_id, now=now)
        if fresh:
            doc = json.loads(trips.artifact_read(slug, name))
            return {"key": key, "skipped": True, "rows": len(doc["rows"])}
    prefs_doc = prefs.show()
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, node_id)  # before the network fetch
    leg = _leg_for_key(slug, key, trip, node["endpoint_source"])
    store = connect(cache_db(), now=now)
    base_generation = store.pin_generation(slug, key)  # pinned before the fetch
    watermark = store.global_watermark()  # global run-start watermark, before the fetch
    client = SeatsClient(store, floor=quota_floor)
    swept = _sweep_leg(client, leg, trip["plan"])
    complete = swept["completeness"] in GENERATION_CUTTING_COMPLETENESS
    sweep = {
        "trip_slug": slug,
        "label": key,
        "kind": leg["kind"],
        "params": {"origins": leg.get("origins"), "dests": leg.get("dests")},
        "started_at": now().isoformat(),
    }
    sources = trip["plan"].get("sources", [])
    scope = _scope(leg, swept["rows"], swept["searched"], sources or None)
    result = store.ingest(
        swept["rows"],
        sweep=sweep,
        complete=complete,
        base_generation=base_generation,
        scope=scope,
        watermark=watermark,
    )
    # The store returns only in-scope disappearances; out-of-scope prior rows carry
    # forward into the new generation, kept visible but never disclosed here.
    superseded_ids = sorted(row["id"] for row in result["superseded"])
    provenance = {
        "source": leg.get("source") or ",".join(sources) or "all",
        "fetched_at": now().isoformat(),
        "searched": swept["searched"],
        "completeness": swept["completeness"],
        "expanded_origins": swept["expanded_origins"],
    }
    if superseded_ids:
        provenance["superseded_rows"] = {
            "count": len(superseded_ids),
            "ids": superseded_ids[:50],
        }
    envelope = {
        "provenance": provenance,
        "search_states": swept["search_states"],
        "rows": swept["rows"],
    }
    trips.artifact_write(slug, name, json.dumps(envelope, separators=(",", ":")))
    quota = _quota_remaining(store)
    trips.phase_done(slug, node_id, quota_after=quota, inputs_fp=inputs_fp, now=now)
    return {
        "key": key,
        "rows": result["rows"],
        "new": result["new"],
        "calls": swept["calls"],
        "completeness": swept["completeness"],
        "quota_remaining": quota,
    }


def _quota_remaining(store: Any) -> int | None:
    try:
        return store.latest_quota()["remaining"]
    except NoData:
        return None


sweep_group = click.Group("sweep", help="Derive and run leg sweeps from the compiled graph.")


@sweep_group.command("plan")
@click.argument("slug")
@map_errors
def _plan_cmd(slug: str) -> None:
    graph = trips.compile_graph(slug)
    keys = [n["id"].split(":", 1)[1] for n in graph["nodes"] if n["kind"] == "sweep"]
    emit({"keys": keys})


@sweep_group.command("run")
@click.argument("slug")
@click.argument("key")
@click.option("--refresh", is_flag=True)
@click.option("--quota-floor", type=int, default=DEFAULT_QUOTA_FLOOR)
@map_errors
def _run_cmd(slug: str, key: str, refresh: bool, quota_floor: int) -> None:
    from getaway.constants import EXIT_AUTH, EXIT_NO_DATA

    try:
        emit(run(slug, key, refresh=refresh, quota_floor=quota_floor))
    except AuthError as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_AUTH) from err
    except NoData as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_NO_DATA) from err
