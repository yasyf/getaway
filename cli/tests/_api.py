"""Builders for seats.aero API rows and cache seeding used across engine tests."""

import datetime as dt
from collections.abc import Callable
from typing import Any

from getaway import paths
from getaway.store import connect

CABINS = ("Y", "W", "J", "F")


def api_row(
    row_id: str,
    origin: str,
    dest: str,
    date: str,
    source: str,
    cabins: dict[str, dict[str, Any]],
    *,
    origin_region: str = "North America",
    dest_region: str = "Asia",
    updated_at: str = "2026-07-12T00:00:00Z",
) -> dict:
    """One /search-shaped availability row. MileageCost is a string, as the API returns."""
    row: dict[str, Any] = {
        "ID": row_id,
        "Route": {
            "OriginAirport": origin,
            "DestinationAirport": dest,
            "OriginRegion": origin_region,
            "DestinationRegion": dest_region,
            "Distance": 5000,
            "Source": source,
        },
        "Date": date,
        "Source": source,
        "UpdatedAt": updated_at,
    }
    for letter in CABINS:
        spec = cabins.get(letter, {})
        present = bool(spec)
        row[f"{letter}Available"] = spec.get("available", present)
        row[f"{letter}MileageCost"] = spec.get("mileage", "0")
        row[f"{letter}RemainingSeats"] = spec.get("seats", 0)
        row[f"{letter}Airlines"] = spec.get("airlines", "")
        row[f"{letter}Direct"] = spec.get("direct", False)
    return row


def seed(
    slug: str,
    label: str,
    kind: str,
    rows: list[dict],
    now: Callable[[], dt.datetime],
) -> None:
    """Ingest rows into the trip's cache with sweep membership under ``label``."""
    store = connect(paths.cache_db(), now=now)
    sweep = {
        "trip_slug": slug,
        "label": label,
        "kind": kind,
        "params": {"label": label},
        "started_at": now().isoformat(),
    }
    store.ingest(rows, sweep=sweep)
