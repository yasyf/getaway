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
    SEARCH_PAGE_SIZE,
    SOFT_DATE_SEARCH_PADDING_DAYS,
    SWEEP_PAGE_BUDGET,
)
from getaway.paths import UsageError, cache_db, emit, map_errors, utcnow
from getaway.seats import AuthError, PaginationError, SeatsClient
from getaway.store import NoData, QuotaFloorError, connect

Spec = dict[str, Any]
Leg = dict[str, Any]


def _region_slug(region: str) -> str:
    return region.lower().replace(" ", "-")


def _shift_date(date_str: str, days: int) -> str:
    return (dt.date.fromisoformat(date_str) + dt.timedelta(days=days)).isoformat()


def derive_specs(trip: dict, prefs_doc: dict) -> list[Spec]:
    """Outbound-leg sweep specs from the plan: buckets and gateways search, program sweeps pull
    bulk availability. Endpoints stay as written (region pseudo-codes included) — the server
    expands them and the leg records the concrete set at sweep time."""
    plan = trip["plan"]
    if not plan:
        return []
    specs: list[Spec] = []
    for bucket in plan.get("buckets", []):
        specs.append(
            {
                "label": bucket["name"],
                "kind": "search",
                "origins": plan["origins"],
                "dests": bucket["dests"],
            }
        )
    for sweep in plan.get("program_sweeps", []):
        region = sweep.get("dest_region") or sweep.get("origin_region")
        spec: Spec = {
            "label": f"{sweep['source']}-{_region_slug(region)}",
            "kind": "availability",
            "source": sweep["source"],
        }
        if "dest_region" in sweep:
            spec["dest_region"] = sweep["dest_region"]
        if "origin_region" in sweep:
            spec["origin_region"] = sweep["origin_region"]
        specs.append(spec)
    hybrid = plan.get("hybrid")
    if hybrid:
        specs.append(
            {
                "label": "gateways",
                "kind": "search",
                "origins": plan["origins"],
                "dests": hybrid["gateways"],
            }
        )
    return specs


def _sweep_window(trip: dict, role: str) -> tuple[str, str]:
    """Soft windows sweep the trip window plus padding; a confirmed date constraint is exact."""
    plan = trip["plan"]
    window = trip["window"]
    constraints = plan.get("constraints", {})
    pad = SOFT_DATE_SEARCH_PADDING_DAYS
    if role == "return":
        hard = constraints.get("return_arrival_by")
        if hard:
            return window["start"], hard["latest_local_date"]
        return window["start"], _shift_date(window["end"], pad)
    hard = constraints.get("outbound_departure_window")
    if hard:
        return hard["start"], hard["end"]
    return _shift_date(window["start"], -pad), _shift_date(window["end"], pad)


def _return_endpoints(slug: str, trip: dict) -> tuple[list[str], list[str]]:
    """Return-leg origins resolve at run time from the outbound shortlist's reached destinations
    (plus onward_dests and any explicit plan.return override); dests are home. Explicit
    plan.return values replace the derived ones."""
    plan = trip["plan"]
    override = plan.get("return") or {}
    shortlist = json.loads(trips.artifact_read(slug, "legs/outbound/shortlist.json"))
    reached = {c["dest"] for c in shortlist["candidates"]}
    onward = plan.get("hybrid", {}).get("onward_dests", [])
    origins = override.get("origins") or sorted(reached | set(onward))
    dests = override.get("dests") or plan["origins"]
    return origins, dests


def _leg_for_key(slug: str, key: str, trip: dict, prefs_doc: dict) -> Leg:
    plan = trip["plan"]
    window = _sweep_window(trip, "return" if key == "return" else "outbound")
    if key == "return":
        origins, dests = _return_endpoints(slug, trip)
        return {
            "id": "sweep:return",
            "role": "return",
            "kind": "search",
            "path": "/search",
            "origins": origins,
            "dests": dests,
            "endpoints": origins,
            "endpoint_field": "origin",
            "window": window,
            "source": None,
        }
    leg_role, _, label = key.partition(":")
    if leg_role != "outbound":
        raise UsageError(f"unknown sweep key: {key!r}")
    if label == "onward":
        hybrid = plan["hybrid"]
        gateway_doc = json.loads(trips.artifact_read(slug, "legs/outbound/shortlist-gateway.json"))
        gateways = sorted({c["dest"] for c in gateway_doc["candidates"]})
        if not gateways:
            raise NoData(f"onward sweep for {slug!r} has no gateways: shortlist-gateway is empty")
        return {
            "id": "sweep:outbound:onward",
            "role": "outbound",
            "kind": "search",
            "path": "/search",
            "origins": gateways,
            "dests": hybrid["onward_dests"],
            "endpoints": hybrid["onward_dests"],
            "endpoint_field": "dest",
            "window": window,
            "source": None,
        }
    spec = next((s for s in derive_specs(trip, prefs_doc) if s["label"] == label), None)
    if spec is None:
        raise UsageError(f"no sweep spec for label {label!r}")
    if spec["kind"] == "search":
        return {
            "id": f"sweep:outbound:{label}",
            "role": "outbound",
            "kind": "search",
            "path": "/search",
            "origins": spec["origins"],
            "dests": spec["dests"],
            "endpoints": spec["dests"],
            "endpoint_field": "dest",
            "window": window,
            "source": None,
        }
    return {
        "id": f"sweep:outbound:{label}",
        "role": "outbound",
        "kind": "availability",
        "path": "/availability",
        "source": spec["source"],
        "origin_region": spec.get("origin_region"),
        "dest_region": spec.get("dest_region"),
        "endpoints": [label],
        "endpoint_field": None,
        "window": window,
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
            if rows:
                searched.append({"start": start, "end": end})
                if isinstance(err.cause, QuotaFloorError):
                    stop = ("partial", "quota_budget")
                else:
                    stop = ("partial", str(err.cause), "retryable")
            elif isinstance(err.cause, QuotaFloorError):
                stop = ("not_run", "quota_budget")
            else:
                stop = ("failed", str(err.cause), "retryable")
            break
        except QuotaFloorError:
            stop = ("not_run", "quota_budget")
            break
        except httpx.HTTPError as err:
            stop = ("failed", str(err), "retryable")
            break
        searched.append({"start": start, "end": end})
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


def _artifact_name(key: str) -> str:
    if key == "return":
        return "legs/return/sweep.json"
    _, _, label = key.partition(":")
    return f"legs/outbound/sweep-{label}.json"


def run(
    slug: str,
    key: str,
    refresh: bool = False,
    quota_floor: int = DEFAULT_QUOTA_FLOOR,
    now: Callable[[], dt.datetime] = utcnow,
) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    node_id = f"sweep:{key}"
    name = _artifact_name(key)
    if not refresh:  # freshness self-skip checks before reserving quota
        fresh, _ = trips.phase_check(slug, node_id, now=now)
        if fresh:
            doc = json.loads(trips.artifact_read(slug, name))
            return {"key": key, "skipped": True, "rows": len(doc["rows"])}
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, node_id)  # before the network fetch
    leg = _leg_for_key(slug, key, trip, prefs_doc)
    store = connect(cache_db(), now=now)
    client = SeatsClient(store, floor=quota_floor)
    swept = _sweep_leg(client, leg, trip["plan"])
    sweep = {
        "trip_slug": slug,
        "label": key,
        "kind": leg["kind"],
        "params": {"origins": leg.get("origins"), "dests": leg.get("dests")},
        "started_at": now().isoformat(),
    }
    result = store.ingest(swept["rows"], sweep=sweep)
    sources = trip["plan"].get("sources", [])
    start, end = leg["window"]
    superseded_ids = sorted(
        row["id"] for row in result["superseded"] if start <= row["date"] <= end
    )
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
