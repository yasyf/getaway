from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

import httpx

from getaway import keys

BASE_URL = "https://serpapi.com/search"
API_KEY_ENV = "SERPAPI_API_KEY"
PREFS_KEY = "serpapi_op_ref"
HTTP_TIMEOUT = httpx.Timeout(30.0)
TRAVEL_CLASS = {
    "economy": 1,
    "premium": 2,
    "business": 3,
    "first": 4,
}

Row = dict[str, Any]


class SerpApiError(RuntimeError):
    """A sanitized SerpApi request failure."""


def _normalize_leg(segment: Row) -> SimpleNamespace:
    return SimpleNamespace(
        airline=SimpleNamespace(name=segment["airline"]),
        flight_number=segment["flight_number"],
        departure_airport=segment["departure_airport"]["id"],
        arrival_airport=segment["arrival_airport"]["id"],
        departure_datetime=dt.datetime.fromisoformat(segment["departure_airport"]["time"]),
        arrival_datetime=dt.datetime.fromisoformat(segment["arrival_airport"]["time"]),
    )


def _normalize_option(option: Row) -> SimpleNamespace:
    flights = option["flights"]
    return SimpleNamespace(
        price=option["price"],
        currency="USD",
        duration=option["total_duration"],
        stops=len(flights) - 1,
        legs=[_normalize_leg(segment) for segment in flights],
    )


def resolve_api_key() -> str:
    return keys.resolve(API_KEY_ENV, PREFS_KEY, "serpapi")


def resolve_api_key_if_available() -> str | None:
    try:
        return resolve_api_key()
    except keys.AuthError:
        return None


def search(
    origin: str,
    dest: str,
    date: str,
    cabin: str,
    api_key: str | None = None,
) -> list[SimpleNamespace]:
    key = api_key if api_key is not None else resolve_api_key()
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": dest,
        "outbound_date": date,
        "type": 2,
        "currency": "USD",
        "travel_class": TRAVEL_CLASS[cabin],
        "api_key": key,
    }
    try:
        response = httpx.get(BASE_URL, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 401:
            raise keys.AuthError("SerpApi rejected the API credential") from None
        raise SerpApiError(
            f"SerpApi request failed: HTTP {err.response.status_code} at {BASE_URL}"
        ) from None
    except httpx.HTTPError as err:
        raise SerpApiError(
            f"SerpApi request failed: {type(err).__name__} at {BASE_URL}"
        ) from None

    payload = response.json()
    options = payload.get("best_flights", []) + payload.get("other_flights", [])
    try:
        results = [
            _normalize_option(option) for option in options if option.get("price") is not None
        ]
    except (KeyError, TypeError, ValueError) as err:
        raise SerpApiError(f"SerpApi response malformed: {type(err).__name__}: {err}") from None
    return sorted(results, key=lambda result: result.price)
