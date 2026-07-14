"""Deterministic per-journey fit facts and mandatory preference-miss annotations.

seats.aero timestamps are local wall clocks with a misleading ``Z`` (seats._strip_z drops it,
leaving naive local time). The discipline here: compare calendar preferences in endpoint-local
dates, take elapsed flight time from ``TotalDuration``, and only ever subtract two timestamps that
share one airport (hence one timezone) — never a cross-airport pair. Phase 3 composes journeys and
calls :func:`journey_fit`; ranking (factors.py) weighs what these primitives report and the
renderer always shows the misses. Nothing here gates.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

from getaway.constants import CABIN_PREFIX

Detail = dict[str, Any]
Leg = dict[str, Any]

DAY_TOKENS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _date(local_ts: str) -> dt.date:
    return dt.date.fromisoformat(local_ts[:10])


def _weekday(local_ts: str) -> str:
    return DAY_TOKENS[_date(local_ts).weekday()]


def preferred_cabin(trip: dict) -> str:
    pref = trip["plan"].get("preferences", {}).get("cabin")
    return pref["value"] if pref else trip["cabin"]


def _resolved_departure_days(trip: dict, prefs_doc: dict) -> list[str]:
    pref = trip["plan"].get("preferences", {}).get("departure_days")
    return list(pref["value"]) if pref else list(prefs_doc["departure_days"])


def _leg_endpoints(detail: Detail) -> tuple[str, str, str, str]:
    segments = detail["segments"]
    first, last = segments[0], segments[-1]
    return (first["origin"], last["dest"], first["departs_local"], last["arrives_local"])


def _window_overshoot(dep_local: str, arr_local: str, window: dict) -> dict:
    start, end = dt.date.fromisoformat(window["start"]), dt.date.fromisoformat(window["end"])
    return {
        "days_before_start": max(0, (start - _date(dep_local)).days),
        "days_after_end": max(0, (_date(arr_local) - end).days),
    }


def _cabin_fit(detail: Detail, preferred_letter: str) -> dict:
    segments = detail["segments"]
    mixed = sum(seg["duration_minutes"] for seg in segments if seg["cabin"] != preferred_letter)
    return {
        "matched": all(seg["cabin"] == preferred_letter for seg in segments),
        "mixed_cabin_minutes": mixed,
    }


def _connections(detail: Detail) -> dict:
    segments = detail["segments"]
    airports = [seg["dest"] for seg in segments[:-1]]
    airport_change = any(a["dest"] != b["origin"] for a, b in zip(segments, segments[1:]))
    return {
        "stops": len(segments) - 1,
        "layover_minutes": sum(detail["layovers"]),
        "airports": airports,
        "airport_change": airport_change,
        "self_transfer": airport_change,
    }


def _seat_sufficiency(detail: Detail, party: int) -> dict:
    seats = detail["remaining_seats"]
    if not seats:  # zero or missing reads as unknown for some programs
        state = "unknown"
    elif seats < party:
        state = "insufficient"
    else:
        state = "sufficient"
    return {"state": state, "count": seats}


def _cache_age_hours(fetched_at: str | None, now: dt.datetime) -> float | None:
    if fetched_at is None:
        return None
    return round((now - dt.datetime.fromisoformat(fetched_at)).total_seconds() / 3600, 2)


def _cash_leg_facts(leg: Leg) -> dict:
    # No seats.aero detail — detail-dependent facts stay absent (unknown = neutral).
    cash = leg["cash"]
    return {
        "role": leg["role"],
        "mode": "cash",
        "origin": leg["origin"],
        "dest": leg["dest"],
        "elapsed_minutes": cash["duration_minutes"],
        "stops": cash["stops"],
        "airline": cash["airline"],
        "departs_local": cash["departs_local"],
        "arrives_local": cash["arrives_local"],
    }


def _leg_facts(
    leg: Leg,
    window: dict,
    preferred_letter: str,
    departure_days: list[str],
    party: int,
    now: dt.datetime,
) -> dict:
    if leg.get("mode") == "cash":
        return _cash_leg_facts(leg)
    detail = leg["detail"]
    origin, dest, dep_local, arr_local = _leg_endpoints(detail)
    token = _weekday(dep_local)
    return {
        "role": leg["role"],
        "mode": "award",
        "origin": origin,
        "dest": dest,
        "departs_local": dep_local,
        "arrives_local": arr_local,
        "elapsed_minutes": detail["total_duration"],
        "window_overshoot": _window_overshoot(dep_local, arr_local, window),
        "departure_day": {"token": token, "match": token in departure_days},
        "cabin": _cabin_fit(detail, preferred_letter),
        "connections": _connections(detail),
        "seat_sufficiency": _seat_sufficiency(detail, party),
        "mileage": {"program": leg["source"], "miles": detail["mileage"]},
        "cache_age_hours": _cache_age_hours(leg.get("fetched_at"), now),
    }


def _mileage_components(legs: list[Leg]) -> dict:
    by_program: dict[str, int] = {}
    for leg in legs:
        if leg.get("mode") == "cash":
            continue
        by_program[leg["source"]] = by_program.get(leg["source"], 0) + leg["detail"]["mileage"]
    single = len(by_program) == 1
    return {
        "by_program": by_program,
        "funding_mode": "single_program" if single else "mixed_programs",
        "same_program_total": sum(by_program.values()) if single else None,
    }


def journey_fit(
    trip: dict,
    prefs_doc: dict,
    legs: list[Leg],
    now: Callable[[], dt.datetime],
) -> dict:
    """Fit facts + mandatory preference misses for one composed journey.

    ``legs`` is an ordered list of typed legs. An award leg is
    ``{role, mode:"award", detail, source, fetched_at?}`` where ``detail`` is an expanded
    seats.aero itinerary (segments carry local wall-clock times and per-segment cabins); a cash
    leg is ``{role, mode:"cash", origin, dest, cash}`` and contributes only elapsed time and cost —
    its detail-dependent facts stay absent (unknown = neutral). The outbound leg is required; a
    non-return onward leg makes this a hybrid journey and a return leg makes it round-trip.
    """
    window = trip["window"]
    party = trip["party"]
    preferred_letter = CABIN_PREFIX[preferred_cabin(trip)]
    departure_days = _resolved_departure_days(trip, prefs_doc)
    now_dt = now()

    outbound_side = [leg for leg in legs if leg["role"] != "return"]
    return_leg = next((leg for leg in legs if leg["role"] == "return"), None)
    leg_facts = [
        _leg_facts(leg, window, preferred_letter, departure_days, party, now_dt) for leg in legs
    ]

    trip_length_days = None
    away_nights = None
    if return_leg is not None:
        # Effective destination is the last pre-return leg; a cash onward leg has no arrival clock.
        gateway, last_outbound = outbound_side[0], outbound_side[-1]
        _, _, ob_dep, _ = _leg_endpoints(gateway["detail"])
        _, _, ret_dep, ret_arr = _leg_endpoints(return_leg["detail"])
        trip_length_days = (_date(ret_arr) - _date(ob_dep)).days
        if last_outbound.get("mode") != "cash":
            _, _, _, dest_arr = _leg_endpoints(last_outbound["detail"])
            away_nights = (_date(ret_dep) - _date(dest_arr)).days

    fit_facts = {
        "legs": leg_facts,
        "trip_length_days": trip_length_days,
        "away_nights": away_nights,
        "mileage": _mileage_components(legs),
    }
    misses = _preference_misses(fit_facts, trip["plan"])
    return {"fit_facts": fit_facts, "preference_misses": misses}


def _miss(code: str, delta: object, annotation: str) -> dict:
    return {"code": code, "delta": delta, "annotation": annotation}


def _preference_misses(fit_facts: dict, plan: dict) -> list[dict]:
    preferences = plan.get("preferences", {})
    legs = {leg["role"]: leg for leg in fit_facts["legs"]}
    outbound = legs["outbound"]
    return_leg = legs.get("return")
    misses: list[dict] = []

    if "outbound_departure_window" in preferences:
        early = outbound["window_overshoot"]["days_before_start"]
        if early:
            note = f"departs {early} day(s) before your window"
            misses.append(_miss("outbound_departure_window", -early, note))

    if "return_arrival_by" in preferences and return_leg is not None:
        value = preferences["return_arrival_by"]["value"]["latest_local_date"]
        latest = dt.date.fromisoformat(value)
        delta = (_date(return_leg["arrives_local"]) - latest).days
        if delta > 0:
            note = f"returns {delta} day(s) past your {latest} preference"
            misses.append(_miss("return_arrival_by", delta, note))

    if "trip_length" in preferences and fit_facts["trip_length_days"] is not None:
        target = preferences["trip_length"]["value"]["days"]
        delta = fit_facts["trip_length_days"] - target
        if delta != 0:
            longer = "longer" if delta > 0 else "shorter"
            note = f"{abs(delta)} day(s) {longer} than your {target}-day target"
            misses.append(_miss("trip_length", delta, note))

    if "departure_days" in preferences and not outbound["departure_day"]["match"]:
        want = preferences["departure_days"]["value"]
        token = outbound["departure_day"]["token"]
        note = f"departs {token}, not your preferred {want}"
        misses.append(_miss("departure_days", token, note))

    if "cabin" in preferences:
        for leg in fit_facts["legs"]:
            if "cabin" not in leg:
                continue
            mixed = leg["cabin"]["mixed_cabin_minutes"]
            if mixed:
                misses.append(
                    _miss(
                        "cabin",
                        mixed,
                        f"{leg['role']} leg has {mixed} min outside your preferred cabin",
                    )
                )

    if "mileage_target" in preferences:
        target = preferences["mileage_target"]["value"]["miles"]
        total = sum(fit_facts["mileage"]["by_program"].values())
        delta = total - target
        if delta > 0:
            misses.append(
                _miss("mileage_target", delta, f"{delta} miles over your {target}-mile target")
            )

    return misses
