import datetime as dt
from collections.abc import Callable

import pytest

from getaway import fit

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
WINDOW = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def seg(origin: str, dest: str, dep: str, arr: str, minutes: int, cabin: str = "J") -> dict:
    return {
        "origin": origin,
        "dest": dest,
        "departs_local": dep,
        "arrives_local": arr,
        "duration_minutes": minutes,
        "cabin": cabin,
        "carrier": "UA",
        "flight_number": "UA1",
        "aircraft": "77W",
    }


def leg(
    role: str,
    segments: list[dict],
    *,
    source: str = "united",
    mileage: int = 80000,
    layovers: list[int] | None = None,
    total_duration: int | None = None,
    seats: int = 2,
    fetched_at: str | None = None,
) -> dict:
    total = total_duration
    if total is None:
        total = sum(s["duration_minutes"] for s in segments)
    return {
        "role": role,
        "source": source,
        "fetched_at": fetched_at,
        "detail": {
            "id": f"{role}1",
            "mileage": mileage,
            "remaining_seats": seats,
            "total_duration": total,
            "segments": segments,
            "layovers": layovers or [],
        },
    }


def plan_legs(*, returns: bool) -> list[dict]:
    """The plan's leg intents — only the return-side gate (last leg targets ``$origins``) matters to
    the fit engine, which anchors facts by the typed-leg positions passed to ``journey_fit``."""
    outbound = {"id": "outbound", "origins": ["SFO"], "dests": ["NRT"]}
    if returns:
        return [outbound, {"id": "return", "dests": "$origins"}]
    return [outbound]


def trip(
    preferences: dict | None = None,
    cabin: str = "business",
    window: dict | None = None,
    party: int = 1,
    trip_type: str = "round_trip",
) -> dict:
    legs = plan_legs(returns=trip_type != "one_way")
    return {
        "cabin": cabin,
        "party": party,
        "window": window or WINDOW,
        "plan": {"legs": legs, "preferences": preferences or {}},
    }


PREFS = {"departure_days": []}


def _outbound(
    *,
    source: str = "united",
    total_duration: int | None = None,
    seats: int = 2,
    fetched_at: str | None = None,
) -> dict:
    hop = seg("SFO", "NRT", "2026-09-05T11:00:00", "2026-09-06T15:00:00", 660)
    return leg(
        "outbound",
        [hop],
        source=source,
        total_duration=total_duration,
        seats=seats,
        fetched_at=fetched_at,
    )


def _return(
    arr: str = "2026-09-15T09:00:00", dep: str = "2026-09-14T18:00:00", *, source: str = "united"
) -> dict:
    return leg("return", [seg("NRT", "SFO", dep, arr, 600)], source=source)


def test_cross_timezone_elapsed_uses_total_duration() -> None:
    facts = fit.journey_fit(
        trip(trip_type="one_way"), PREFS, [_outbound(total_duration=660)], clock()
    )["fit_facts"]
    ob = facts["legs"][0]
    # naive arrival-minus-departure would read ~28h across the dateline; elapsed is TotalDuration
    assert ob["elapsed_minutes"] == 660
    assert ob["departs_local"].startswith("2026-09-05")
    assert ob["arrives_local"].startswith("2026-09-06")


def test_trip_length_and_away_nights_are_calendar_spans() -> None:
    legs = [_outbound(), _return(dep="2026-09-14T18:00:00", arr="2026-09-15T09:00:00")]
    facts = fit.journey_fit(trip(), PREFS, legs, clock())["fit_facts"]
    assert facts["trip_length_days"] == 10  # 09-05 home departure to 09-15 home arrival
    assert facts["away_nights"] == 8  # 09-06 NRT arrival to 09-14 NRT departure


def test_one_way_has_no_round_trip_spans() -> None:
    facts = fit.journey_fit(trip(trip_type="one_way"), PREFS, [_outbound()], clock())[
        "fit_facts"
    ]
    assert facts["trip_length_days"] is None
    assert facts["away_nights"] is None


def test_return_side_facts_follow_the_variant_plan_legs() -> None:
    # Variant facts: an optional-leg skip is scored under its own plan legs, so a skipped homeward
    # leg reads one-way spans while the full variant keeps its round trip.
    round_trip = fit.journey_fit(
        trip(), PREFS, [_outbound(), _return(arr="2026-09-15T09:00:00")], clock()
    )["fit_facts"]
    assert round_trip["trip_length_days"] == 10
    skip_variant = {
        "cabin": "business",
        "party": 1,
        "window": WINDOW,
        "plan": {"legs": plan_legs(returns=False)},
    }
    skipped = fit.journey_fit(skip_variant, PREFS, [_outbound()], clock())["fit_facts"]
    assert skipped["trip_length_days"] is None
    assert skipped["away_nights"] is None


def _cash_onward(
    origin: str = "NRT",
    dest: str = "OKA",
    dep: str = "2026-09-08T09:00:00",
    arr: str = "2026-09-08T12:00:00",
    connections: list[str] | None = None,
) -> dict:
    conns = connections or []
    return {
        "role": "onward",
        "mode": "cash",
        "origin": origin,
        "dest": dest,
        "cash": {
            "duration_minutes": 180,
            "stops": len(conns),
            "connections": conns,
            "airline": "JL",
            "departs_local": dep,
            "arrives_local": arr,
        },
    }


@pytest.mark.parametrize(
    (
        "legs",
        "trip_type",
        "expected_misses",
        "expected_trip_length_days",
        "expected_away_nights",
    ),
    [
        pytest.param(
            [_outbound(), _return()],
            "round_trip",
            [("return_arrival_by", 1)],
            10,
            8,
            id="round-trip-first-outbound-last-return",
        ),
        pytest.param(
            [_outbound()],
            "one_way",
            [],
            None,
            None,
            id="one-way-no-return",
        ),
        pytest.param(
            [_outbound(), _cash_onward()],
            "one_way",
            [],
            None,
            None,
            id="one-way-hybrid-onward-is-not-return",
        ),
        pytest.param(
            [_outbound(), _cash_onward(), _return()],
            "round_trip",
            [("return_arrival_by", 1)],
            10,
            6,
            id="three-leg-round-trip-last-pre-return-is-destination",
        ),
    ],
)
def test_journey_spans_and_return_misses_use_plan_type_and_leg_positions(
    legs: list[dict],
    trip_type: str,
    expected_misses: list[tuple[str, int]],
    expected_trip_length_days: int | None,
    expected_away_nights: int | None,
) -> None:
    preferences = {
        "return_arrival_by": {
            "value": {"latest_local_date": "2026-09-14"},
            "priority": "primary",
        }
    }
    result = fit.journey_fit(
        trip(preferences, trip_type=trip_type), PREFS, legs, clock()
    )
    fit_facts = result["fit_facts"]
    assert fit_facts["trip_length_days"] == expected_trip_length_days
    assert fit_facts["away_nights"] == expected_away_nights
    assert [(miss["code"], miss["delta"]) for miss in result["preference_misses"]] == (
        expected_misses
    )


def test_cash_leg_facts_expose_connections_per_airport() -> None:
    legs = [_outbound(), _cash_onward(connections=["FUK"])]
    facts = fit.journey_fit(trip(trip_type="one_way"), PREFS, legs, clock())["fit_facts"]
    cash_facts = facts["legs"][-1]
    assert cash_facts["mode"] == "cash"
    assert cash_facts["stops"] == 1
    assert cash_facts["connections"] == ["FUK"]


def test_away_nights_uses_cash_leg_real_arrival_clock() -> None:
    legs = [
        _outbound(),
        _cash_onward(arr="2026-09-08T12:00:00"),
        _return(dep="2026-09-12T16:00:00", arr="2026-09-13T10:00:00"),
    ]
    facts = fit.journey_fit(trip(), PREFS, legs, clock())["fit_facts"]
    assert facts["away_nights"] == 4  # 09-08 cash arrival -> 09-12 return departure


def _cash_leg(
    role: str,
    origin: str,
    dest: str,
    dep: str,
    arr: str,
    *,
    duration: int = 180,
    connections: list[str] | None = None,
) -> dict:
    conns = connections or []
    return {
        "role": role,
        "mode": "cash",
        "origin": origin,
        "dest": dest,
        "cash": {
            "duration_minutes": duration,
            "stops": len(conns),
            "connections": conns,
            "airline": "JL",
            "departs_local": dep,
            "arrives_local": arr,
        },
    }


def test_cash_first_positioning_leg_reads_its_clocks_without_crash() -> None:
    # legs[0] is cash: journey_fit reads its quote clocks for the door-to-door span, no KeyError.
    legs = [
        _cash_leg("positioning", "SFO", "LAX", "2026-09-03T08:00:00", "2026-09-03T11:00:00"),
        leg("outbound", [seg("LAX", "NRT", "2026-09-05T11:00:00", "2026-09-06T15:00:00", 660)]),
        _return(),
    ]
    facts = fit.journey_fit(trip(), PREFS, legs, clock())["fit_facts"]
    assert facts["trip_length_days"] == 12  # cash departure 09-03 -> return arrival 09-15
    assert facts["away_nights"] == 8  # NRT arrival 09-06 -> return departure 09-14
    assert facts["legs"][0]["mode"] == "cash"


def test_cash_last_home_hop_reads_its_clocks_without_crash() -> None:
    # legs[-1] is a cash home hop: the return-side spans read its quote clocks, no KeyError.
    legs = [
        _outbound(),
        _cash_leg(
            "return", "NRT", "SFO", "2026-09-14T18:00:00", "2026-09-15T09:00:00", duration=600
        ),
    ]
    facts = fit.journey_fit(trip(), PREFS, legs, clock())["fit_facts"]
    assert facts["trip_length_days"] == 10  # award departure 09-05 -> cash home arrival 09-15
    assert facts["away_nights"] == 8
    assert facts["legs"][-1]["mode"] == "cash"


def test_cash_last_home_hop_carries_return_arrival_miss() -> None:
    prefs_pref = {
        "return_arrival_by": {"value": {"latest_local_date": "2026-09-14"}, "priority": "primary"}
    }
    legs = [
        _outbound(),
        _cash_leg(
            "return", "NRT", "SFO", "2026-09-14T18:00:00", "2026-09-15T09:00:00", duration=600
        ),
    ]
    result = fit.journey_fit(trip(prefs_pref), PREFS, legs, clock())
    misses = {m["code"]: m for m in result["preference_misses"]}
    assert misses["return_arrival_by"]["delta"] == 1  # cash home arrives 09-15, one day past


def test_departure_days_pref_skips_cash_first_leg() -> None:
    # A cash first leg carries no weekday fact — the departure_days miss stays neutral, not a crash.
    prefs_pref = {"departure_days": {"value": ["Mon"], "priority": "note"}}
    legs = [
        _cash_leg("positioning", "SFO", "LAX", "2026-09-03T08:00:00", "2026-09-03T11:00:00"),
        _outbound(),
    ]
    result = fit.journey_fit(trip(prefs_pref, trip_type="one_way"), PREFS, legs, clock())
    assert all(m["code"] != "departure_days" for m in result["preference_misses"])


@pytest.mark.parametrize(
    ("segment_cabin", "expected_below_minutes", "expected_misses"),
    [
        (
            "Y",
            150,
            [
                {
                    "code": "cabin",
                    "delta": 150,
                    "annotation": "outbound leg has 150 min below your preferred cabin",
                }
            ],
        ),
        ("F", 0, []),
    ],
    ids=["economy-below-business", "first-above-business"],
)
def test_cabin_below_minutes(
    segment_cabin: str, expected_below_minutes: int, expected_misses: list[dict]
) -> None:
    segments = [
        seg("SFO", "HND", "2026-09-05T11:00:00", "2026-09-06T14:00:00", 660, cabin="J"),
        seg(
            "HND",
            "OKA",
            "2026-09-06T16:00:00",
            "2026-09-06T18:30:00",
            150,
            cabin=segment_cabin,
        ),
    ]
    preferences = {"cabin": {"value": "business", "priority": "note"}}
    result = fit.journey_fit(
        trip(preferences, trip_type="one_way"),
        PREFS,
        [leg("outbound", segments, layovers=[120])],
        clock(),
    )
    cabin = result["fit_facts"]["legs"][0]["cabin"]
    assert cabin["matched"] is False
    assert cabin["below_cabin_minutes"] == expected_below_minutes
    assert result["preference_misses"] == expected_misses


def test_connections_flags_airport_change() -> None:
    segments = [
        seg("SFO", "NRT", "2026-09-05T11:00:00", "2026-09-06T14:00:00", 660),
        seg("HND", "OKA", "2026-09-06T18:00:00", "2026-09-06T20:30:00", 150),
    ]
    conn = fit.journey_fit(
        trip(trip_type="one_way"),
        PREFS,
        [leg("outbound", segments, layovers=[240])],
        clock(),
    )
    facts = conn["fit_facts"]["legs"][0]["connections"]
    assert facts["stops"] == 1
    assert facts["airports"] == ["NRT"]
    assert facts["airport_change"] is True  # arrives NRT, departs HND
    assert facts["layover_minutes"] == 240


def test_seat_sufficiency_states() -> None:
    def state(seats: int, party: int) -> str:
        legs = [_outbound(seats=seats)]
        facts = fit.journey_fit(
            trip(party=party, trip_type="one_way"), PREFS, legs, clock()
        )["fit_facts"]
        return facts["legs"][0]["seat_sufficiency"]["state"]

    assert state(2, 1) == "sufficient"
    assert state(1, 2) == "insufficient"
    assert state(0, 2) == "unknown"  # zero reads as unknown for some programs


def test_mileage_single_vs_mixed_program() -> None:
    single = fit.journey_fit(
        trip(), PREFS, [_outbound(source="united"), _return(source="united")], clock()
    )["fit_facts"]["mileage"]
    assert single["funding_mode"] == "single_program"
    assert single["same_program_total"] == 160000

    mixed = fit.journey_fit(
        trip(), PREFS, [_outbound(source="united"), _return(source="aeroplan")], clock()
    )["fit_facts"]["mileage"]
    assert mixed["funding_mode"] == "mixed_programs"
    assert mixed["same_program_total"] is None
    assert mixed["by_program"] == {"united": 80000, "aeroplan": 80000}


def test_cache_age_from_fetched_at() -> None:
    legs = [_outbound(fetched_at="2026-07-13T06:00:00+00:00")]
    facts = fit.journey_fit(trip(trip_type="one_way"), PREFS, legs, clock())["fit_facts"]
    assert facts["legs"][0]["cache_age_hours"] == 6.0


def test_preferred_cabin_resolves_from_preference_over_trip() -> None:
    prefs_pref = {"cabin": {"value": "economy", "priority": "note"}}
    segments = [seg("SFO", "NRT", "2026-09-05T11:00:00", "2026-09-06T15:00:00", 660, cabin="Y")]
    facts = fit.journey_fit(
        trip(prefs_pref, trip_type="one_way"),
        PREFS,
        [leg("outbound", segments)],
        clock(),
    )
    assert facts["fit_facts"]["legs"][0]["cabin"]["matched"] is True  # economy preference honored


# --- preference misses (the renderer always shows these) ---


def test_preference_misses_use_first_outbound_and_last_return_positions() -> None:
    plan = {
        "legs": plan_legs(returns=True),
        "preferences": {
            "return_arrival_by": {
                "value": {"latest_local_date": "2026-09-14"},
                "priority": "primary",
            },
            "departure_days": {"value": ["Mon"], "priority": "note"},
        },
    }
    fit_facts = {
        "legs": [
            {
                "role": "onward",
                "departs_local": "2026-09-05T11:00:00",
                "departure_day": {"token": "Sat", "match": False},
            },
            {
                "role": "return",
                "arrives_local": "2026-09-14T09:00:00",
            },
            {
                "role": "outbound",
                "arrives_local": "2026-09-16T09:00:00",
                "departure_day": {"token": "Mon", "match": True},
            },
        ],
        "trip_length_days": None,
        "mileage": {"by_program": {}},
    }

    assert fit._preference_misses(fit_facts, plan) == [
        {
            "code": "return_arrival_by",
            "delta": 2,
            "annotation": "returns 2 day(s) past your 2026-09-14 preference",
        },
        {
            "code": "departure_days",
            "delta": "Sat",
            "annotation": "departs Sat, not your preferred ['Mon']",
        },
    ]


def test_return_arrival_miss_named() -> None:
    prefs_pref = {
        "return_arrival_by": {"value": {"latest_local_date": "2026-09-14"}, "priority": "primary"}
    }
    legs = [_outbound(), _return(arr="2026-09-15T09:00:00")]
    result = fit.journey_fit(trip(prefs_pref), PREFS, legs, clock())
    misses = {m["code"]: m for m in result["preference_misses"]}
    assert misses["return_arrival_by"]["delta"] == 1
    assert "past your 2026-09-14 preference" in misses["return_arrival_by"]["annotation"]


def test_beyond_window_journey_composes_and_carries_its_miss() -> None:
    # The "back Tuesday but perfect" case: an out-of-window return still produces fit facts and a
    # named miss instead of vanishing.
    prefs_pref = {
        "return_arrival_by": {"value": {"latest_local_date": "2026-09-14"}, "priority": "primary"}
    }
    legs = [_outbound(), _return(arr="2026-09-16T09:00:00")]
    result = fit.journey_fit(trip(prefs_pref), PREFS, legs, clock())
    assert result["fit_facts"]["legs"]  # the journey composed
    misses = {m["code"]: m for m in result["preference_misses"]}
    assert misses["return_arrival_by"]["delta"] == 2


def test_trip_length_miss_named() -> None:
    prefs_pref = {
        "trip_length": {"value": {"days": 7, "basis": "elapsed_door_to_door"}, "priority": "note"}
    }
    legs = [_outbound(), _return(dep="2026-09-14T18:00:00", arr="2026-09-15T09:00:00")]
    result = fit.journey_fit(trip(prefs_pref), PREFS, legs, clock())
    misses = {m["code"]: m for m in result["preference_misses"]}
    assert misses["trip_length"]["delta"] == 3  # 10-day journey vs 7-day target


def test_no_misses_when_within_preferences() -> None:
    prefs_pref = {
        "return_arrival_by": {"value": {"latest_local_date": "2026-09-16"}, "priority": "primary"}
    }
    legs = [_outbound(), _return(arr="2026-09-15T09:00:00")]
    result = fit.journey_fit(trip(prefs_pref), PREFS, legs, clock())
    assert result["preference_misses"] == []


def test_departure_day_miss_uses_trip_preference() -> None:
    prefs_pref = {"departure_days": {"value": ["Mon"], "priority": "note"}}
    result = fit.journey_fit(
        trip(prefs_pref, trip_type="one_way"), PREFS, [_outbound()], clock()
    )
    misses = {m["code"]: m for m in result["preference_misses"]}
    assert "departure_days" in misses  # 2026-09-05 is a Saturday, not Monday


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        pytest.param(
            {"start": "2026-09-06", "end": "2026-09-10"},
            [
                {
                    "code": "outbound_departure_window",
                    "delta": -1,
                    "annotation": "departs 1 day(s) before your window start",
                }
            ],
            id="before-start",
        ),
        pytest.param({"start": "2026-09-05", "end": "2026-09-10"}, [], id="at-start"),
        pytest.param({"start": "2026-09-01", "end": "2026-09-10"}, [], id="inside"),
        pytest.param({"start": "2026-09-01", "end": "2026-09-05"}, [], id="at-end"),
        pytest.param(
            {"start": "2026-09-01", "end": "2026-09-04"},
            [
                {
                    "code": "outbound_departure_window",
                    "delta": 1,
                    "annotation": "departs 1 day(s) past your window end",
                }
            ],
            id="past-end-1",
        ),
        pytest.param(
            {"start": "2026-08-30", "end": "2026-09-02"},
            [
                {
                    "code": "outbound_departure_window",
                    "delta": 3,
                    "annotation": "departs 3 day(s) past your window end",
                }
            ],
            id="past-end-3",
        ),
    ],
)
def test_outbound_window_miss_is_preference_relative_both_sides(
    window: dict, expected: list[dict]
) -> None:
    # The preference window, not trip["window"], drives the miss — and both the early and late
    # sides read against the preference's own start/end. Outbound departs 2026-09-05.
    prefs_pref = {"outbound_departure_window": {"value": window, "priority": "primary"}}
    result = fit.journey_fit(
        trip(prefs_pref, trip_type="one_way"), PREFS, [_outbound()], clock()
    )
    assert result["preference_misses"] == expected


def test_no_outbound_window_miss_without_preference() -> None:
    result = fit.journey_fit(
        trip({}, trip_type="one_way"), PREFS, [_outbound()], clock()
    )
    assert result["preference_misses"] == []
