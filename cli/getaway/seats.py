from __future__ import annotations

import datetime as dt
import functools
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

import click
import httpx

from getaway import paths
from getaway.constants import CABIN_PREFIX, EXIT_AUTH
from getaway.store import NoData, Store, connect, parse_duration

BASE_URL = "https://seats.aero/partnerapi"
AUTH_HEADER = "Partner-Authorization"
API_KEY_ENV = "SEATS_AERO_API_KEY"
RATE_LIMIT_HEADER = "X-RateLimit-Remaining"
DEFAULT_TAKE = 500
_OP_PREFIX = "op://"
_KEY_RE = re.compile(r"[!-~]+")

Row = dict[str, Any]


class AuthError(Exception):
    """No usable seats.aero credential could be resolved."""


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


def _normalize_trip(availability_id: str, payload: Row) -> Row:
    trip = payload["data"][0]
    segments = sorted(trip["AvailabilitySegments"], key=lambda seg: seg["Order"])
    return {
        "id": availability_id,
        "mileage": trip["MileageCost"],
        "total_taxes": trip["TotalTaxes"],
        "taxes_currency": trip["TaxesCurrency"],
        "remaining_seats": trip["RemainingSeats"],
        "total_duration": trip["TotalDuration"],
        "segments": [_normalize_segment(seg) for seg in segments],
        "layovers": _layovers(segments),
        "booking_links": payload["booking_links"],
        "raw": payload,
    }


def _prefs_op_ref() -> str | None:
    path = paths.prefs_path()
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("op_ref")


def _op_read(ref: str) -> str:
    result = subprocess.run(["op", "read", ref], capture_output=True, text=True)
    if result.returncode != 0:
        raise AuthError("failed to resolve the API key from the configured 1Password reference")
    return result.stdout.strip()


def _validate_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key):
        raise AuthError("resolved seats.aero API key must be printable ASCII without whitespace")
    return key


def resolve_api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if key:
        return _validate_key(key)
    ref = _prefs_op_ref()
    if not ref:
        raise AuthError("no seats.aero API key: set SEATS_AERO_API_KEY or a preferences op_ref")
    if not ref.startswith(_OP_PREFIX):
        raise AuthError("preferences op_ref must be a 1Password op:// reference")
    return _validate_key(_op_read(ref))


class SeatsClient:
    def __init__(self, store: Store, api_key: str | None = None) -> None:
        self._store = store
        key = api_key if api_key is not None else resolve_api_key()
        self._client = httpx.Client(headers={AUTH_HEADER: key})

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

    def trip_detail(self, availability_id: str) -> Row:
        payload = self._get(f"/trips/{availability_id}", {})
        return _normalize_trip(availability_id, payload)

    def _get(self, path: str, params: Row) -> Any:
        response = self._client.get(f"{BASE_URL}{path}", params=params)
        self._record_quota(path, response)
        response.raise_for_status()
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
                    rows.append(row)
            skip += len(page_rows)
            if not payload.get("hasMore"):
                break
            cursor = payload["cursor"]
        return rows

    def _record_quota(self, path: str, response: httpx.Response) -> None:
        remaining = response.headers.get(RATE_LIMIT_HEADER)
        if remaining is None:
            return
        endpoint = "/" + path.strip("/").split("/")[0]
        self._store.record_quota(endpoint, int(remaining))


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


def _map_auth(fn: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except AuthError as err:
            click.echo(str(err), err=True)
            raise SystemExit(EXIT_AUTH) from err

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
@_map_auth
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
) -> None:
    store = _open_store()
    client = SeatsClient(store)
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
@_map_auth
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
) -> None:
    store = _open_store()
    client = SeatsClient(store)
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
@_map_auth
def routes_cmd(source: str, origin_region: str | None, dest_region: str | None) -> None:
    store = _open_store()
    client = SeatsClient(store)
    rows = client.routes(source)
    if origin_region:
        rows = [row for row in rows if row["OriginRegion"] == origin_region]
    if dest_region:
        rows = [row for row in rows if row["DestinationRegion"] == dest_region]
    click.echo(json.dumps({"routes": rows, "count": len(rows)}))


@click.command("expand")
@click.argument("availability_id")
@click.option("--fresh-within", default="6h")
@click.option("--refresh", is_flag=True)
@_map_auth
def expand_cmd(availability_id: str, fresh_within: str, refresh: bool) -> None:
    store = _open_store()
    if not refresh:
        cached = store.trip_detail_get(availability_id, fresh_within=parse_duration(fresh_within))
        if cached is not None:
            click.echo(json.dumps(cached))
            return
    client = SeatsClient(store)
    normalized = client.trip_detail(availability_id)
    store.trip_detail_put(availability_id, normalized)
    click.echo(json.dumps(normalized))
