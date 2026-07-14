from __future__ import annotations

import datetime as dt
import functools
import json
from collections.abc import Callable, Sequence
from typing import Any

import click
import httpx

from getaway import keys, paths
from getaway.constants import (
    CABIN_PREFIX,
    DEFAULT_QUOTA_FLOOR,
    EXIT_AUTH,
    EXIT_NEGATIVE,
    EXIT_NO_DATA,
)
from getaway.keys import AuthError
from getaway.store import NoData, QuotaFloorError, Store, connect, parse_duration

BASE_URL = "https://seats.aero/partnerapi"
AUTH_HEADER = "Partner-Authorization"
API_KEY_ENV = "SEATS_AERO_API_KEY"
RATE_LIMIT_HEADER = "X-RateLimit-Remaining"
DEFAULT_TAKE = 500
# Bound each request well under the 5-min quota-reservation TTL; a hung socket must
# free its reservation before the stale-prune cutoff or two callers could double-spend.
HTTP_TIMEOUT = httpx.Timeout(30.0)
# /search and /availability return MileageCost as a string; /trips as an int.
# Normalize at the client boundary so integers flow everywhere downstream.
_MILEAGE_FIELDS = tuple(f"{cabin}MileageCost" for cabin in CABIN_PREFIX.values())

Row = dict[str, Any]


def cabin_rows(row: Row) -> list[Row]:
    return [
        {
            "cabin": cabin,
            "available": bool(row[f"{cabin}Available"]),
            "mileage_cost": int(row[f"{cabin}MileageCost"]),
            "remaining_seats": row[f"{cabin}RemainingSeats"],
            "airlines": row[f"{cabin}Airlines"],
            "direct": bool(row[f"{cabin}Direct"]),
        }
        for cabin in CABIN_PREFIX.values()
    ]


def _normalize_availability_row(row: Row) -> Row:
    for field in _MILEAGE_FIELDS:
        row[field] = int(row[field])
    return row


def _endpoint(path: str) -> str:
    return "/" + path.strip("/").split("/")[0]


def _remaining_header(response: httpx.Response) -> int | None:
    remaining = response.headers.get(RATE_LIMIT_HEADER)
    return int(remaining) if remaining is not None else None


def _strip_z(timestamp: str) -> str:
    return timestamp[:-1] if timestamp.endswith("Z") else timestamp


def _normalize_segment(segment: Row) -> Row:
    flight_number = segment["FlightNumber"]
    return {
        "origin": segment["OriginAirport"],
        "dest": segment["DestinationAirport"],
        "departs_local": _strip_z(segment["DepartsAt"]),
        "arrives_local": _strip_z(segment["ArrivesAt"]),
        "flight_number": flight_number,
        "carrier": flight_number[:2],
        "aircraft": segment["AircraftName"],
        "duration_minutes": segment["Duration"],
        "cabin": CABIN_PREFIX[segment["Cabin"]],
    }


def _layovers(segments: Sequence[Row]) -> list[int]:
    layovers = []
    for prev, nxt in zip(segments, segments[1:]):
        arrives = dt.datetime.fromisoformat(_strip_z(prev["ArrivesAt"]))
        departs = dt.datetime.fromisoformat(_strip_z(nxt["DepartsAt"]))
        layovers.append(int((departs - arrives).total_seconds() // 60))
    return layovers


def _trip_in_cabin(trip: Row, cabin: str) -> bool:
    return all(CABIN_PREFIX[seg["Cabin"]] == cabin for seg in trip["AvailabilitySegments"])


def _normalize_trip(availability_id: str, payload: Row, cabin: str) -> Row:
    matching = [trip for trip in payload["data"] if _trip_in_cabin(trip, cabin)]
    if not matching:
        raise NoData(f"no {cabin} itinerary for {availability_id}")
    trip = min(matching, key=lambda t: t["MileageCost"])
    segments = sorted(trip["AvailabilitySegments"], key=lambda seg: seg["Order"])
    return {
        "id": availability_id,
        "mileage": trip["MileageCost"],
        "total_taxes": trip["TotalTaxes"],
        # Absent on some programs (observed live: american, 2026-07-13) — None means unreported.
        "taxes_currency": trip.get("TaxesCurrency"),
        "remaining_seats": trip["RemainingSeats"],
        "total_duration": trip["TotalDuration"],
        "segments": [_normalize_segment(seg) for seg in segments],
        "layovers": _layovers(segments),
        "booking_links": payload["booking_links"],
        "raw": payload,
    }


def resolve_api_key() -> str:
    return keys.resolve(API_KEY_ENV, "op_ref", "seats.aero")


class SeatsClient:
    def __init__(
        self, store: Store, api_key: str | None = None, floor: int = DEFAULT_QUOTA_FLOOR
    ) -> None:
        self._store = store
        self._floor = floor
        key = keys.validate(api_key, "seats.aero") if api_key is not None else resolve_api_key()
        self._client = httpx.Client(headers={AUTH_HEADER: key}, timeout=HTTP_TIMEOUT)

    def search(
        self,
        origins: Sequence[str],
        dests: Sequence[str],
        start: str | None = None,
        end: str | None = None,
        cabins: Sequence[str] | None = None,
        sources: Sequence[str] | None = None,
        carriers: Sequence[str] | None = None,
        direct: bool = False,
        order_by: str | None = None,
        take: int = DEFAULT_TAKE,
        pages: int = 1,
    ) -> list[Row]:
        params: Row = {
            "origin_airport": ",".join(origins),
            "destination_airport": ",".join(dests),
        }
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end
        if cabins:
            params["cabins"] = ",".join(cabins)
        if sources:
            params["sources"] = ",".join(sources)
        if carriers:
            params["carriers"] = ",".join(carriers)
        if direct:
            params["only_direct_flights"] = "true"
        if order_by:
            params["order_by"] = order_by
        return self._paginate("/search", params, take, pages)

    def availability(
        self,
        source: str,
        cabin: str | None = None,
        start: str | None = None,
        end: str | None = None,
        origin_region: str | None = None,
        dest_region: str | None = None,
        take: int = DEFAULT_TAKE,
        pages: int = 1,
    ) -> list[Row]:
        params: Row = {"source": source}
        if cabin:
            params["cabin"] = cabin
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end
        if origin_region:
            params["origin_region"] = origin_region
        if dest_region:
            params["destination_region"] = dest_region
        return self._paginate("/availability", params, take, pages)

    def routes(self, source: str) -> list[Row]:
        return self._get("/routes", {"source": source})

    def trip_detail(self, availability_id: str, cabin: str) -> Row:
        payload = self._get(f"/trips/{availability_id}", {})
        return _normalize_trip(availability_id, payload, cabin)

    def _get(self, path: str, params: Row) -> Any:
        # Reconcile in finally so a failed request frees its reservation. A lost
        # response records no header; the next call's MIN-over-today reconcile
        # self-corrects. Bounded, not a retry gap.
        endpoint = _endpoint(path)
        token = self._store.reserve_quota(self._floor)
        response: httpx.Response | None = None
        try:
            response = self._client.get(f"{BASE_URL}{path}", params=params)
        finally:
            remaining = _remaining_header(response) if response is not None else None
            self._store.reconcile_quota(token, endpoint, remaining)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            if response.status_code in (401, 403):
                raise AuthError("seats.aero rejected the API credential") from err
            raise
        return response.json()

    def _paginate(self, path: str, params: Row, take: int, pages: int) -> list[Row]:
        rows: list[Row] = []
        seen: set[str] = set()
        cursor: Any = None
        skip = 0
        for _ in range(pages):
            page_params = dict(params)
            page_params["take"] = take
            if cursor is not None:
                page_params["cursor"] = cursor
                page_params["skip"] = skip
            payload = self._get(path, page_params)
            page_rows = payload["data"]
            for row in page_rows:
                if row["ID"] not in seen:
                    seen.add(row["ID"])
                    rows.append(_normalize_availability_row(row))
            skip += len(page_rows)
            if not payload.get("hasMore"):
                break
            cursor = payload["cursor"]
        return rows


def _open_store() -> Store:
    return connect(paths.cache_db())


def _quota_remaining(store: Store) -> int | None:
    try:
        return store.latest_quota()["remaining"]
    except NoData:
        return None


def _sweep_spec(
    trip: str | None, label: str | None, kind: str, params: Row
) -> dict[str, Any] | None:
    if trip is None or label is None:
        return None
    return {
        "trip_slug": trip,
        "label": label,
        "kind": kind,
        "params": params,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _map_errors(fn: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except AuthError as err:
            click.echo(str(err), err=True)
            raise SystemExit(EXIT_AUTH) from err
        except QuotaFloorError as err:
            click.echo(str(err), err=True)
            raise SystemExit(EXIT_NEGATIVE) from err

    return wrapper


@click.command("search")
@click.option("--origin", "origins", multiple=True, required=True)
@click.option("--dest", "dests", multiple=True, required=True)
@click.option("--start")
@click.option("--end")
@click.option("--cabin", "cabins", multiple=True)
@click.option("--source", "sources", multiple=True)
@click.option("--carrier", "carriers", multiple=True)
@click.option("--direct", is_flag=True)
@click.option("--order-by")
@click.option("--take", type=int, default=DEFAULT_TAKE)
@click.option("--pages", type=int, default=1)
@click.option("--trip")
@click.option("--label")
@click.option("--quota-floor", type=int, default=DEFAULT_QUOTA_FLOOR)
@_map_errors
def search_cmd(
    origins: tuple[str, ...],
    dests: tuple[str, ...],
    start: str | None,
    end: str | None,
    cabins: tuple[str, ...],
    sources: tuple[str, ...],
    carriers: tuple[str, ...],
    direct: bool,
    order_by: str | None,
    take: int,
    pages: int,
    trip: str | None,
    label: str | None,
    quota_floor: int,
) -> None:
    store = _open_store()
    client = SeatsClient(store, floor=quota_floor)
    rows = client.search(
        list(origins),
        list(dests),
        start=start,
        end=end,
        cabins=list(cabins) or None,
        sources=list(sources) or None,
        carriers=list(carriers) or None,
        direct=direct,
        order_by=order_by,
        take=take,
        pages=pages,
    )
    sweep = _sweep_spec(trip, label, "search", {"origins": list(origins), "dests": list(dests)})
    result = store.ingest(rows, sweep=sweep)
    click.echo(json.dumps({**result, "quota_remaining": _quota_remaining(store)}))


@click.command("availability")
@click.option("--source", required=True)
@click.option("--cabin")
@click.option("--start")
@click.option("--end")
@click.option("--origin-region")
@click.option("--dest-region")
@click.option("--take", type=int, default=DEFAULT_TAKE)
@click.option("--pages", type=int, default=1)
@click.option("--trip")
@click.option("--label")
@click.option("--quota-floor", type=int, default=DEFAULT_QUOTA_FLOOR)
@_map_errors
def availability_cmd(
    source: str,
    cabin: str | None,
    start: str | None,
    end: str | None,
    origin_region: str | None,
    dest_region: str | None,
    take: int,
    pages: int,
    trip: str | None,
    label: str | None,
    quota_floor: int,
) -> None:
    store = _open_store()
    client = SeatsClient(store, floor=quota_floor)
    rows = client.availability(
        source,
        cabin=cabin,
        start=start,
        end=end,
        origin_region=origin_region,
        dest_region=dest_region,
        take=take,
        pages=pages,
    )
    result = store.ingest(rows, sweep=_sweep_spec(trip, label, "availability", {"source": source}))
    click.echo(json.dumps({**result, "quota_remaining": _quota_remaining(store)}))


@click.command("routes")
@click.argument("source")
@click.option("--origin-region")
@click.option("--dest-region")
@click.option("--quota-floor", type=int, default=DEFAULT_QUOTA_FLOOR)
@_map_errors
def routes_cmd(
    source: str, origin_region: str | None, dest_region: str | None, quota_floor: int
) -> None:
    store = _open_store()
    client = SeatsClient(store, floor=quota_floor)
    rows = client.routes(source)
    if origin_region:
        rows = [row for row in rows if row["OriginRegion"] == origin_region]
    if dest_region:
        rows = [row for row in rows if row["DestinationRegion"] == dest_region]
    click.echo(json.dumps({"routes": rows, "count": len(rows)}))


@click.command("expand")
@click.argument("availability_id")
@click.option("--cabin", required=True)
@click.option("--fresh-within", default="6h")
@click.option("--refresh", is_flag=True)
@click.option("--quota-floor", type=int, default=DEFAULT_QUOTA_FLOOR)
@_map_errors
def expand_cmd(
    availability_id: str, cabin: str, fresh_within: str, refresh: bool, quota_floor: int
) -> None:
    """Emit the lowest-mileage bookable itinerary in --cabin for an availability id.

    Selects among the /trips itineraries the cheapest one flown entirely in the
    requested cabin (Y/W/J/F) and prints the normalized JSON. Exits EXIT_NO_DATA
    (4) when the availability has no itinerary in that cabin.
    """
    store = _open_store()
    if not refresh:
        cached = store.trip_detail_get(availability_id, fresh_within=parse_duration(fresh_within))
        if cached is not None:
            click.echo(json.dumps(cached))
            return
    client = SeatsClient(store, floor=quota_floor)
    try:
        normalized = client.trip_detail(availability_id, cabin)
    except NoData as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_NO_DATA) from err
    store.trip_detail_put(availability_id, normalized)
    click.echo(json.dumps(normalized))
