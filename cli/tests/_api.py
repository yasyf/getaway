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


def sweep_envelope(
    rows: list[dict] | None = None,
    *,
    source: str = "all",
    expanded_origins: list[str] | None = None,
    search_states: dict | None = None,
    completeness: str = "complete",
    searched: list[dict] | None = None,
    fetched_at: str = "2026-07-13T12:00:00+00:00",
    superseded_rows: dict | None = None,
) -> dict:
    """A leg sweep artifact envelope (provenance + search states + rows)."""
    rows = rows or []
    origins = expanded_origins
    if origins is None:
        origins = sorted({row["Route"]["OriginAirport"] for row in rows})
    provenance = {
        "source": source,
        "fetched_at": fetched_at,
        "searched": (
            searched if searched is not None else [{"start": "2026-09-01", "end": "2026-09-14"}]
        ),
        "completeness": completeness,
        "expanded_origins": origins,
    }
    if superseded_rows is not None:
        provenance["superseded_rows"] = superseded_rows
    return {
        "provenance": provenance,
        "search_states": search_states or {},
        "rows": rows,
    }


def shortlist_doc(
    candidates: list[dict] | None = None,
    *,
    considered: int = 0,
    leg: str = "outbound",
    search_states: dict | None = None,
    truncation: dict | None = None,
) -> dict:
    """A leg shortlist artifact matching the write-boundary schema."""
    return {
        "candidates": candidates or [],
        "considered": considered,
        "search_states": search_states or {},
        "leg": leg,
        "truncation": truncation or {},
    }


def expand_doc(
    journeys: list[dict] | None = None,
    *,
    unpaired_outbounds: list[dict] | None = None,
    gated: list[dict] | None = None,
    search_states: dict | None = None,
    leg_states: dict | None = None,
    truncation: dict | None = None,
    leads: list[dict] | None = None,
) -> dict:
    """A composed-journeys artifact matching the expand write-boundary schema."""
    provenance = {"fetched_at": "2026-07-13T12:00:00+00:00", "quota_stopped": False}
    if truncation is not None:
        provenance["truncation"] = truncation
    doc = {
        "journeys": journeys or [],
        "unpaired_outbounds": unpaired_outbounds or [],
        "gated": gated or [],
        "search_states": search_states or {},
        "leg_states": leg_states or {},
        "provenance": provenance,
    }
    if leads is not None:
        doc["leads"] = leads
    return doc
