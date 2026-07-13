"""Stays node — rooms.aero lodging intervals, row ingest, and board threading (B4).

The stays node is agent-shaped: a sequential browser walk of rooms.aero (``command=None``
like assess, routed opus/xhigh) that the walker preflights for a seeded Pro session. Interval
derivation and row ingest around that walk are deterministic CLI. ``stays intervals`` reads
the ranked journeys and derives each presented journey's check-in / check-out / nights from
its real paired timestamps — a Tuesday return honestly adds the extra hotel night — and
defers lodging (never guessing a checkout) for unpaired leads, open jaws whose outbound
destination differs from the return origin without an explicit checkout, and past check-ins.
The walker walks each walkable interval and pipes normalized rows to ``stays ingest``, which
validates the row shape and the six rooms.aero hotel slugs, writes ``stays.json`` namespaced by
journey id with full provenance, and stamps the node. ``factors.finalize`` threads each board
journey's stay (or its deferral) onto the board.

Per the Phase-1 rooms.aero recon: per-night points/cash are source of truth (cash in cents,
property-local currency) and any stay total is an estimate; ``last_checked_at`` is real UTC;
stays past rooms.aero's hard five-consecutive-night block cap clamp to five with
``night_clamped`` disclosed. The node spends zero seats.aero quota.
"""

import datetime as dt
import json
import sys
from collections.abc import Callable
from typing import Any

import click

from getaway import prefs, registry, trips
from getaway.constants import PRESENTATION_LIMIT
from getaway.paths import (
    UsageError,
    emit,
    map_errors,
    require_int,
    require_int_or_none,
    require_keys,
    require_str,
    utcnow,
)

Journey = dict[str, Any]

ROOMS_AERO_MAX_NIGHTS = 5  # rooms.aero hard five-consecutive-night block cap
SEARCH_STATES = frozenset(
    {
        "complete",
        "searched_empty",
        "night_clamped",
        "bot_wall",
        "logged_out",
        "date_in_past",
        "geocode_miss",
        "failed",
    }
)
AWARD_CLASSES = frozenset({"standard", "suite"})
SESSIONS = frozenset({"pro", "anonymous"})


def rooms_aero_programs() -> frozenset[str]:
    return frozenset(slug for slug, row in registry.programs().items() if row["rooms_aero"])


def _outbound_and_return(journey: Journey) -> tuple[list[dict], dict | None]:
    """Split the ordered legs at the return. Everything before the return leg is the outbound
    side (a direct journey's single leg, or a hybrid's gateway + onward legs); the effective
    destination is its LAST leg's dest — the onward_dest, never the gateway."""
    legs = journey["fit_facts"]["legs"]
    idx = next((i for i, leg in enumerate(legs) if leg["role"] == "return"), len(legs))
    return legs[:idx], (legs[idx] if idx < len(legs) else None)


def _home_origin(journey: Journey) -> str:
    return journey["fit_facts"]["legs"][0]["origin"]


def _origin_local_today(origin: str, now_dt: dt.datetime) -> dt.date:
    """Origin-local "today" for the past-check-in guard, mirroring the bridge A8 rule; a home
    origin absent from the hub UTC-offset map falls back to UTC (a whole-day staleness guard)."""
    from getaway import bridge

    local = bridge._origin_local_today(origin, now_dt)
    return local if local is not None else now_dt.date()


def _deferred(reason: str, dest: str, extra: dict | None = None) -> dict:
    return {
        "disposition": "deferred",
        "reason": reason,
        "destination_airport": dest,
        **(extra or {}),
    }


def derive_interval(journey: Journey, plan: dict, today: dt.date) -> dict:
    """Deterministic lodging interval for one composed journey.

    Check-in is the destination-local arrival date; check-out is the return-departure local
    date, or an explicit ``plan.lodging.checkout`` override (the only checkout a one-way or an
    open jaw whose return origin differs from the outbound destination can carry). Returns a
    ``walk`` disposition with the interval, or a ``deferred`` disposition naming why lodging
    can't be searched — never a guessed checkout.
    """
    outbound, return_leg = _outbound_and_return(journey)
    effective = outbound[-1]  # last pre-return leg — the stay's real destination
    dest = effective["dest"]
    # A cash hop with no observed arrival — never guess a check-in.
    if "arrives_local" not in effective:
        return _deferred("unknown_arrival", dest)
    check_in = effective["arrives_local"][:10]
    explicit = plan.get("lodging", {}).get("checkout")

    if explicit is not None:
        check_out = explicit
    elif return_leg is not None and return_leg["origin"] == dest:
        check_out = return_leg["departs_local"][:10]
    else:
        return _deferred("no_checkout", dest)

    nights = (dt.date.fromisoformat(check_out) - dt.date.fromisoformat(check_in)).days
    if nights < 1:
        return _deferred("invalid_interval", dest)
    night_clamped = nights > ROOMS_AERO_MAX_NIGHTS
    if night_clamped:
        nights = ROOMS_AERO_MAX_NIGHTS
        check_out = (dt.date.fromisoformat(check_in) + dt.timedelta(days=nights)).isoformat()
    if dt.date.fromisoformat(check_in) < today:
        return _deferred("date_in_past", dest, {"check_in": check_in})
    return {
        "disposition": "walk",
        "destination_airport": dest,
        "interval": {
            "check_in": check_in,
            "check_out": check_out,
            "nights": nights,
            "night_clamped": night_clamped,
        },
    }


def _worklist_entry(jid: str, derived: dict) -> dict:
    dest = derived["destination_airport"]
    if derived["disposition"] == "walk":
        interval = derived["interval"]
        return {
            "journey_id": jid,
            "destination_airport": dest,
            "disposition": "walk",
            "interval": interval,
            "search_key": f"{dest}|{interval['check_in']}|{interval['nights']}",
            "lodging_search": None,
        }
    return {
        "journey_id": jid,
        "destination_airport": dest,
        "disposition": "deferred",
        "interval": None,
        "search_key": None,
        "lodging_search": {"state": "deferred", "reason": derived["reason"]},
    }


def _board_journeys(rank_doc: dict) -> list[Journey]:
    """The complete journeys the board presents — the ranked cut plus the notable stretches
    assess pulled from beyond it. Deduplicated; both share the ``{journey, ...}`` entry shape."""
    entries = rank_doc["ranked"][:PRESENTATION_LIMIT] + rank_doc["notable_stretches"]
    seen: set[str] = set()
    journeys: list[Journey] = []
    for entry in entries:
        journey = entry["journey"]
        if journey["id"] in seen:
            continue
        seen.add(journey["id"])
        journeys.append(journey)
    return journeys


def intervals(slug: str, now: Callable[[], dt.datetime] = utcnow) -> dict:
    """Walker worklist: per presented journey, an interval to walk or a lodging deferral.

    Emits the ``inputs_fp`` captured here so ``stays ingest`` can stamp the node against the
    inputs as they stood when the walk began — a mid-walk plan edit then marks the node stale
    rather than stamping over rows derived from the old inputs.
    """
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    rank_doc = json.loads(trips.artifact_read(slug, "rank.json"))
    out: list[dict] = []
    for journey in _board_journeys(rank_doc):
        today = _origin_local_today(_home_origin(journey), now())
        derived = derive_interval(journey, plan, today)
        out.append(_worklist_entry(journey["id"], derived))
    return {
        "slug": slug,
        "generated_at": now().isoformat(),
        "inputs_fp": trips.capture_inputs_fp(trip, prefs_doc, "stays"),
        "journeys": out,
    }


def board_lodging(
    journey: Journey, plan: dict, stays_doc: dict, now: Callable[[], dt.datetime]
) -> dict:
    """Lodging attachment for one board journey: its walked stay, or the deferral reason.

    A journey the walker skipped for a deferral (no checkout, open jaw, past date) never lands
    in ``stays.json``; one it should have walked but did not surfaces as ``not_walked`` — a
    walk gap named honestly, never masked as no availability.
    """
    derived = derive_interval(journey, plan, _origin_local_today(_home_origin(journey), now()))
    if derived["disposition"] == "deferred":
        return {"lodging_search": {"state": "deferred", "reason": derived["reason"]}}
    stay = stays_doc["stays"].get(journey["id"])
    if stay is not None:
        return {"stays": stay}
    return {"lodging_search": {"state": "unavailable", "reason": "not_walked"}}


def unpaired_lodging() -> dict:
    """An unpaired outbound lead has no return leg, so no checkout exists to search against."""
    return {"state": "deferred", "reason": "no_checkout"}


def ingest(
    slug: str,
    raw: str,
    inputs_fp: str | None = None,
    now: Callable[[], dt.datetime] = utcnow,
) -> dict:
    """Validate the walker's normalized rows, write ``stays.json``, and stamp the stays node."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        raise UsageError(f"stays ingest input is not valid JSON: {err}") from err
    if not isinstance(payload, dict) or "stays" not in payload:
        raise UsageError("stays ingest input must be a JSON object with a 'stays' map")
    doc = {"generated_at": now().isoformat(), "stays": payload["stays"]}
    trips.artifact_write(slug, "stays.json", json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, "stays", inputs_fp=inputs_fp, now=now)
    stays = payload["stays"]
    return {"journeys": len(stays), "rooms": sum(len(entry["rooms"]) for entry in stays.values())}


# --- Write-boundary schema (registered in trips.artifact_write) ----------------------------------


def _require_num(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UsageError(f"{label} must be a number")
    return value


def _require_num_or_none(value: object, label: str) -> None:
    if value is not None:
        _require_num(value, label)


def _require_date(value: object, label: str) -> str:
    text = require_str(value, label)
    try:
        dt.date.fromisoformat(text)
    except ValueError as err:
        raise UsageError(f"{label} is not an ISO date: {value!r}") from err
    return text


def _validate_interval(interval: object, label: str) -> None:
    interval = require_keys(interval, {"check_in", "check_out", "nights"}, label)
    _require_date(interval["check_in"], f"{label}.check_in")
    _require_date(interval["check_out"], f"{label}.check_out")
    require_int(interval["nights"], f"{label}.nights")


def _validate_destination(destination: object, label: str) -> None:
    destination = require_keys(
        destination, {"query", "center", "viewport"}, label, optional=frozenset({"airport"})
    )
    require_str(destination["query"], f"{label}.query")
    center = require_keys(destination["center"], {"lat", "lng"}, f"{label}.center")
    _require_num(center["lat"], f"{label}.center.lat")
    _require_num(center["lng"], f"{label}.center.lng")
    viewport = require_keys(
        destination["viewport"], {"sw_lat", "sw_lng", "ne_lat", "ne_lng"}, f"{label}.viewport"
    )
    for key in ("sw_lat", "sw_lng", "ne_lat", "ne_lng"):
        _require_num(viewport[key], f"{label}.viewport.{key}")


def _validate_provenance(provenance: object, label: str) -> None:
    provenance = require_keys(
        provenance,
        {"source", "session", "fetched_at", "search_url", "revalidation", "night_clamped"},
        label,
    )
    if provenance["source"] != "rooms.aero":
        raise UsageError(f"{label}.source must be 'rooms.aero'")
    if provenance["session"] not in SESSIONS:
        raise UsageError(f"{label}.session must be one of {sorted(SESSIONS)}")
    require_str(provenance["fetched_at"], f"{label}.fetched_at")
    require_str(provenance["search_url"], f"{label}.search_url")
    if not isinstance(provenance["night_clamped"], bool):
        raise UsageError(f"{label}.night_clamped must be a boolean")
    revalidation = provenance["revalidation"]
    if revalidation is not None:
        revalidation = require_keys(
            revalidation, {"total", "successful", "queued"}, f"{label}.revalidation"
        )
        for key in ("total", "successful", "queued"):
            require_int(revalidation[key], f"{label}.revalidation.{key}")


def _validate_offer(offer: object, label: str) -> None:
    offer = require_keys(
        offer,
        {
            "award_class",
            "check_in",
            "nights",
            "award_points_per_night",
            "cash_per_night_cents",
            "cents_per_point",
        },
        label,
    )
    if offer["award_class"] not in AWARD_CLASSES:
        raise UsageError(f"{label}.award_class must be one of {sorted(AWARD_CLASSES)}")
    _require_date(offer["check_in"], f"{label}.check_in")
    require_int(offer["nights"], f"{label}.nights")
    require_int_or_none(offer["award_points_per_night"], f"{label}.award_points_per_night")
    require_int_or_none(offer["cash_per_night_cents"], f"{label}.cash_per_night_cents")
    _require_num_or_none(offer["cents_per_point"], f"{label}.cents_per_point")


def _validate_room(room: object, valid_programs: frozenset[str], label: str) -> None:
    room = require_keys(
        room,
        {
            "rooms_aero_id",
            "program",
            "name",
            "lat",
            "lng",
            "currency",
            "last_checked_at",
            "stale",
            "offers",
        },
        label,
    )
    require_str(room["rooms_aero_id"], f"{label}.rooms_aero_id")
    program = require_str(room["program"], f"{label}.program")
    if program not in valid_programs:
        raise UsageError(
            f"{label}.program {program!r} is not a rooms.aero hotel program; "
            f"known rooms.aero programs: {sorted(valid_programs)}"
        )
    require_str(room["name"], f"{label}.name")
    _require_num(room["lat"], f"{label}.lat")
    _require_num(room["lng"], f"{label}.lng")
    require_str(room["currency"], f"{label}.currency")
    require_str(room["last_checked_at"], f"{label}.last_checked_at")
    if not isinstance(room["stale"], bool):
        raise UsageError(f"{label}.stale must be a boolean")
    offers = room["offers"]
    if not isinstance(offers, list):
        raise UsageError(f"{label}.offers must be a list")
    for i, offer in enumerate(offers):
        _validate_offer(offer, f"{label}.offers[{i}]")


def _validate_stay_entry(entry: object, valid_programs: frozenset[str], label: str) -> None:
    entry = require_keys(
        entry, {"interval", "destination", "provenance", "rooms", "search_state"}, label
    )
    _validate_interval(entry["interval"], f"{label}.interval")
    _validate_destination(entry["destination"], f"{label}.destination")
    _validate_provenance(entry["provenance"], f"{label}.provenance")
    if entry["search_state"] not in SEARCH_STATES:
        raise UsageError(f"{label}.search_state must be one of {sorted(SEARCH_STATES)}")
    if not isinstance(entry["rooms"], list):
        raise UsageError(f"{label}.rooms must be a list")
    for i, room in enumerate(entry["rooms"]):
        _validate_room(room, valid_programs, f"{label}.rooms[{i}]")


def validate_stays_doc(doc: object, name: str) -> None:
    """stays.json write-boundary schema: journey-namespaced walk results with provenance, the
    six rooms.aero hotel slugs, and integer cents. Registered in ``trips.artifact_write``."""
    doc = require_keys(doc, {"generated_at", "stays"}, name)
    require_str(doc["generated_at"], f"{name}.generated_at")
    stays = doc["stays"]
    if not isinstance(stays, dict):
        raise UsageError(f"{name}.stays must be an object keyed by journey id")
    valid_programs = rooms_aero_programs()
    for jid, entry in stays.items():
        if not jid:
            raise UsageError(f"{name}.stays has an empty journey id")
        _validate_stay_entry(entry, valid_programs, f"{name}.stays[{jid!r}]")


stays_group = click.Group("stays", help="rooms.aero lodging intervals and row ingest.")


@stays_group.command("intervals")
@click.argument("slug")
@map_errors
def _intervals_cmd(slug: str) -> None:
    emit(intervals(slug))


@stays_group.command("ingest")
@click.argument("slug")
@click.option("--inputs-fp", default=None, help="Freshness fingerprint from `stays intervals`.")
@map_errors
def _ingest_cmd(slug: str, inputs_fp: str | None) -> None:
    emit(ingest(slug, sys.stdin.read(), inputs_fp=inputs_fp))
