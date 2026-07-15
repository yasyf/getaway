import datetime as dt
import json
import random
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import expand_doc

from getaway import factors, prefs, registry, trips
from getaway.paths import UsageError

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"

ALWAYS = {"affordability", "airline_preference", "layovers"}
BUSINESS = {"seat_quality", "cash_anomaly"}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def empty_prefs(**over: object) -> dict:
    doc = {
        "departure_days": [],
        "documents": {"passports": [], "residency": [], "visas": []},
        "status_goals": [],
        "travel_instruments": [],
        "balances": {"programs": {}, "transferable": {}},
    }
    doc.update(over)
    return doc


def monetary(issuer: str = "united", expires: str = "2026-12-01") -> dict:
    # No id: instrument_add generates it; the activation path only reads the type.
    return {
        "type": "monetary_credit",
        "issuer": issuer,
        "amount": 200,
        "currency": "USD",
        "expires": expires,
    }


def active_set(profile: dict) -> set[str]:
    return {fid for fid, spec in profile.items() if spec["active"]}


def test_canonical_dense_ask_active_set() -> None:
    # "warm beachy week, business, avoid seoul/tokyo" — the founding dense one-sentence ask.
    trip = {
        "cabin": "business",
        "vibe": ["warm", "beachy"],
        "avoid_final_destinations": ["ICN", "NRT"],
        "plan": {},
        "judgment": {},
    }
    profile = factors.derive_profile(trip, empty_prefs(), slug=None)
    assert active_set(profile) == ALWAYS | BUSINESS | {"destination_context"}


def _pref(key: str, value: object) -> dict:
    return {"plan": {"preferences": {key: {"value": value, "priority": "secondary"}}}}


@pytest.mark.parametrize(
    ("fid", "trip_over", "prefs_over", "active"),
    [
        pytest.param("departure_days", {}, {"departure_days": ["Mon"]}, True, id="departure-on"),
        pytest.param("departure_days", {}, {}, False, id="departure-off"),
        pytest.param(
            "transit_risk",
            {},
            {"documents": {"passports": ["US"], "residency": [], "visas": []}},
            True,
            id="transit-on",
        ),
        pytest.param("transit_risk", {}, {}, False, id="transit-off"),
        pytest.param(
            "status_earning",
            {},
            {"status_goals": [{"program": "delta", "target": "Platinum", "by": "2026-12-31"}]},
            True,
            id="status-on",
        ),
        pytest.param("status_earning", {}, {}, False, id="status-off"),
        pytest.param(
            "trip_credits", {}, {"travel_instruments": [monetary()]}, True, id="credits-on"
        ),
        pytest.param("trip_credits", {}, {}, False, id="credits-off"),
        pytest.param(
            "seat_quality", {"cabin": "economy"}, {}, False, id="seat-quality-economy-off"
        ),
        pytest.param("cash_anomaly", {"cabin": "first"}, {}, True, id="cash-anomaly-first-on"),
        pytest.param(
            "window_fit",
            _pref("outbound_departure_window", {"start": "2026-09-01", "end": "2026-09-14"}),
            {},
            True,
            id="window-fit-on",
        ),
        pytest.param("window_fit", {}, {}, False, id="window-fit-off"),
        pytest.param(
            "trip_length_fit",
            _pref("trip_length", {"days": 7, "basis": "elapsed"}),
            {},
            True,
            id="trip-length-fit-on",
        ),
        pytest.param(
            "mileage_fit",
            _pref("mileage_target", {"miles": 120000, "scope": "total"}),
            {},
            True,
            id="mileage-fit-on",
        ),
        pytest.param("cabin_fit", _pref("cabin", "business"), {}, True, id="cabin-fit-on"),
        pytest.param(
            "departure_day_fit",
            _pref("departure_days", ["Mon"]),
            {},
            True,
            id="departure-day-fit-on",
        ),
    ],
)
def test_activation_matrix(fid: str, trip_over: dict, prefs_over: dict, active: bool) -> None:
    trip = {"cabin": "business", "vibe": [], "plan": {}, "judgment": {}}
    trip.update(trip_over)
    profile = factors.derive_profile(trip, empty_prefs(**prefs_over), slug=None)
    assert profile[fid]["active"] is active


def pref(value: object, priority: str) -> dict:
    return {"value": value, "priority": priority}


def tier_trip(preferences: dict | None = None, factor_overrides: dict | None = None) -> dict:
    trip: dict = {"cabin": "business", "vibe": [], "plan": {}, "judgment": {}}
    if preferences:
        trip["plan"]["preferences"] = preferences
    if factor_overrides:
        trip["judgment"]["factors"] = factor_overrides
    return trip


WINDOW = {"start": "2026-09-01", "end": "2026-09-14"}


@pytest.mark.parametrize(
    ("preferences", "factor_overrides", "fid", "expected"),
    [
        pytest.param(
            {"cabin": pref("business", "primary")},
            None,
            "cabin_fit",
            "primary",
            id="declared-primary-lifts-secondary-default",
        ),
        pytest.param(
            {"cabin": pref("business", "note")},
            None,
            "cabin_fit",
            "note",
            id="declared-note-drops-secondary-default",
        ),
        pytest.param(
            {"cabin": pref("business", "primary")},
            {"cabin_fit": {"priority": "secondary"}},
            "cabin_fit",
            "secondary",
            id="judgment-override-demotes-declared-primary",
        ),
        pytest.param(
            {"cabin": pref("business", "note")},
            {"cabin_fit": {"priority": "primary"}},
            "cabin_fit",
            "primary",
            id="judgment-override-lifts-declared-note",
        ),
        pytest.param(
            {
                "outbound_departure_window": pref(WINDOW, "secondary"),
                "return_arrival_by": pref("2026-09-20", "primary"),
            },
            None,
            "window_fit",
            "primary",
            id="window-fit-strongest-of-two-keys",
        ),
        pytest.param(
            {
                "outbound_departure_window": pref(WINDOW, "note"),
                "return_arrival_by": pref("2026-09-20", "secondary"),
            },
            None,
            "window_fit",
            "secondary",
            id="window-fit-strongest-other-pair",
        ),
    ],
)
def test_tiers_folds_declared_preference_priorities(
    preferences: dict, factor_overrides: dict | None, fid: str, expected: str
) -> None:
    assert factors._tiers(tier_trip(preferences, factor_overrides))[fid] == expected


def test_tiers_without_declarations_are_registry_defaults() -> None:
    defaults = {f["id"]: f["default_tier"] for f in registry.factors()}
    assert factors._tiers(tier_trip()) == defaults


def test_declared_preference_leaves_other_factor_tiers_untouched() -> None:
    defaults = {f["id"]: f["default_tier"] for f in registry.factors()}
    tiers = factors._tiers(tier_trip({"cabin": pref("business", "primary")}))
    assert tiers == {**defaults, "cabin_fit": "primary"}


def test_derive_profile_shows_effective_tiers() -> None:
    trip = tier_trip(
        {"cabin": pref("business", "primary")},
        {"layovers": {"priority": "primary"}},
    )
    profile = factors.derive_profile(trip, empty_prefs(), slug=None)
    assert profile["cabin_fit"]["priority"] == "primary"  # declared preference priority
    assert profile["layovers"]["priority"] == "primary"  # judgment.factors override
    assert profile["mileage_fit"]["priority"] == "secondary"  # untouched registry default


@pytest.mark.parametrize(
    ("preferences", "factor_overrides", "expected"),
    [
        pytest.param(
            {
                "outbound_departure_window": pref(WINDOW, "primary"),
                "return_arrival_by": pref("2026-09-20", "note"),
            },
            None,
            {"outbound_departure_window"},
            id="mixed-window-only-the-primary-declared-code",
        ),
        pytest.param(
            {
                "outbound_departure_window": pref(WINDOW, "secondary"),
                "return_arrival_by": pref("2026-09-20", "note"),
            },
            {"window_fit": {"priority": "primary"}},
            {"outbound_departure_window", "return_arrival_by"},
            id="judgment-override-up-lifts-both-window-codes",
        ),
        pytest.param(
            {
                "outbound_departure_window": pref(WINDOW, "primary"),
                "return_arrival_by": pref("2026-09-20", "primary"),
            },
            {"window_fit": {"priority": "note"}},
            set(),
            id="judgment-override-down-silences-both-window-codes",
        ),
        pytest.param(
            {"cabin": pref("business", "primary")},
            None,
            {"cabin"},
            id="single-key-declared-primary",
        ),
        pytest.param(
            {"cabin": pref("business", "note")},
            None,
            set(),
            id="single-key-declared-note",
        ),
    ],
)
def test_primary_codes_are_per_code(
    preferences: dict, factor_overrides: dict | None, expected: set[str]
) -> None:
    trip = tier_trip(preferences, factor_overrides)
    active = active_set(factors.derive_profile(trip, empty_prefs(), slug=None))
    assert factors._primary_codes(trip, active) == frozenset(expected)


PLAN = {
    "trip_type": "round_trip",
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
}


@pytest.fixture
def biz_trip(getaway_home: Path) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "party": 2,
            "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10},
            "plan": PLAN,
        },
    )
    return SLUG


def leg(
    role: str,
    source: str,
    *,
    soft: bool = False,
    airlines: str = "UA",
    cabin: str = "J",
    segment_cabins: tuple[str, ...] | None = None,
    departs_local: str = "2026-09-07T09:00",
) -> dict:
    segment_cabins = segment_cabins if segment_cabins is not None else (cabin,)
    return {
        "role": role,
        "id": f"{role}-{source}",
        "cabin": cabin,
        "source": source,
        "mode": "award",
        "soft": soft,
        "airlines": airlines,
        "detail": {
            "segments": [
                {"cabin": segment_cabin, "departs_local": departs_local}
                for segment_cabin in segment_cabins
            ]
        },
        "fetched_at": None,
    }


def cash_onward() -> dict:
    return {
        "role": "onward",
        "id": "onward-cash",
        "cabin": "economy",
        "source": None,
        "mode": "cash",
        "origin": "NRT",
        "dest": "SIN",
        "cash": {},
    }


def journey(
    jid: str,
    by_program: dict[str, int],
    *,
    soft: bool = False,
    airlines: str = "UA",
    cash: list | None = None,
    cash_leg: dict | None = None,
    cabin: str = "J",
    segment_cabins: tuple[str, ...] | None = None,
    departs_local: str = "2026-09-07T09:00",
    kind: str = "round_trip",
    award_legs: list[dict] | None = None,
    misses: list[dict] | None = None,
) -> dict:
    single = len(by_program) == 1
    legs = (
        award_legs
        if award_legs is not None
        else [
            leg(
                "outbound",
                src,
                soft=soft,
                airlines=airlines,
                cabin=cabin,
                segment_cabins=segment_cabins,
                departs_local=departs_local,
            )
            for src in by_program
        ]
    )
    if cash_leg is not None:
        legs.append(cash_leg)
    return {
        "id": jid,
        "kind": kind,
        "legs": legs,
        "fit_facts": {},
        "preference_misses": misses or [],
        "cost": {
            "mileage": {
                "by_program": by_program,
                "funding_mode": "single_program" if single else "mixed_programs",
                "same_program_total": sum(by_program.values()) if single else None,
            },
            "cash": cash or [],
            "taxes": [],
            "unpriced": [],
        },
        "seat_sufficiency": "sufficient",
    }


def verdicts(*items: tuple[str, str]) -> dict:
    return {
        "verdicts": [
            {"factor": f, "leg": None, "verdict": v, "evidence": f"{f} {v}"} for f, v in items
        ]
    }


def do_rank(
    slug: str,
    journeys_list: list[dict],
    *,
    assess: dict | None = None,
    gated: list | None = None,
    notable: list | None = None,
) -> list[dict]:
    trips.artifact_write(
        slug, "expand.json", json.dumps(expand_doc(journeys_list, gated=gated or []))
    )
    if assess is not None or notable is not None:
        trips.artifact_write(
            slug,
            "assess.json",
            json.dumps({"journeys": assess or {}, "notable_stretches": notable or []}),
        )
    return factors.rank(slug, now=clock())


def order(ranked: list[dict]) -> list[str]:
    return [e["journey"]["id"] for e in ranked]


def rank_doc(slug: str) -> dict:
    return json.loads(trips.artifact_read(slug, "rank.json"))


def test_afford_annotates_never_gates(biz_trip: str) -> None:
    prefs.set_balance("united", 10000)  # far short of the award
    ranked = do_rank(biz_trip, [journey("POOR", {"united": 80000})])
    assert order(ranked) == ["POOR"]  # unaffordable, still ranked
    afford = ranked[0]["facts"]["afford"]
    assert afford["covered"] is False
    assert afford["by_program"]["united"]["shortfall"] == 70000


def test_primary_verdict_reorders_within_band(biz_trip: str) -> None:
    a = journey("A", {"united": 80000})
    b = journey("B", {"united": 82000})
    assess = {"A": verdicts(("seat_quality", "demote")), "B": verdicts(("seat_quality", "promote"))}
    assert order(do_rank(biz_trip, [a, b], assess=assess)) == ["B", "A"]


def test_secondary_breaks_primary_ties(biz_trip: str) -> None:
    a = journey("A", {"united": 80000})
    b = journey("B", {"united": 82000})
    # layovers is secondary; with no primary verdicts differing, it decides order within the band.
    assess = {"A": verdicts(("layovers", "demote")), "B": verdicts(("layovers", "promote"))}
    assert order(do_rank(biz_trip, [a, b], assess=assess)) == ["B", "A"]


def test_note_tier_never_reorders(biz_trip: str) -> None:
    a = journey("A", {"united": 78000})
    b = journey("B", {"united": 80000})
    # cash_anomaly is note-tier; a demote on the cheaper A must not reorder.
    assess = {"A": verdicts(("cash_anomaly", "demote")), "B": verdicts(("cash_anomaly", "promote"))}
    assert order(do_rank(biz_trip, [a, b], assess=assess)) == ["A", "B"]


def test_verdict_never_crosses_band(biz_trip: str) -> None:
    a = journey("A", {"united": 80000})
    b = journey("B", {"united": 200000})
    assess = {"B": verdicts(("seat_quality", "promote"))}
    ranked = do_rank(biz_trip, [a, b], assess=assess)
    assert order(ranked) == ["A", "B"]  # B's promote can't cross the mileage band
    assert [e["cost_tier"] for e in ranked] == [0, 1]


def test_same_program_scalar_banding(biz_trip: str) -> None:
    js = [
        journey("A", {"united": 80000}),
        journey("B", {"united": 90000}),
        journey("C", {"united": 200000}),
    ]
    ranked = do_rank(biz_trip, js)
    assert order(ranked) == ["A", "B", "C"]  # A,B band together, cheapest-first; C dominated
    assert [e["cost_tier"] for e in ranked] == [0, 0, 1]


def test_mixed_program_journeys_are_pareto_incomparable(biz_trip: str) -> None:
    # No fungible cross-program scalar: a united-only and a delta-only journey never dominate each
    # other, so both stay on the front. The stable tie-break orders the cheaper total first.
    ranked = do_rank(biz_trip, [journey("U", {"united": 90000}), journey("D", {"delta": 80000})])
    assert order(ranked) == ["D", "U"]
    assert all(e["cost_tier"] == 0 for e in ranked)


def test_tiebreak_distinguishes_swapped_mixed_program_values(biz_trip: str) -> None:
    # Same programs, swapped mileage: the old cross-program sum read both as 100000 (indistinct).
    p = journey("P", {"united": 5000, "delta": 95000})
    q = journey("Q", {"united": 95000, "delta": 5000})
    ranked = do_rank(biz_trip, [p, q])
    assert all(e["cost_tier"] == 0 for e in ranked)  # Pareto-incomparable, same tier
    assert order(ranked) == ["Q", "P"]  # delta 5000 < 95000 on the first shared axis
    assert order(do_rank(biz_trip, [q, p])) == ["Q", "P"]  # independent of input order


def test_tiebreak_orders_same_program_cheaper_first(biz_trip: str) -> None:
    cheap = journey("CHEAP", {"aeroplan": 80000})
    dear = journey("DEAR", {"aeroplan": 82000})
    ranked = do_rank(biz_trip, [dear, cheap])
    assert [e["cost_tier"] for e in ranked] == [0, 0]  # within the mileage band, one tier
    assert order(ranked) == ["CHEAP", "DEAR"]


def test_dominated_mixed_journey_sinks_to_a_later_front(biz_trip: str) -> None:
    cheap = journey("CHEAP", {"united": 40000, "delta": 40000})
    dear = journey("DEAR", {"united": 50000, "delta": 50000})
    ranked = do_rank(biz_trip, [dear, cheap])
    assert order(ranked) == ["CHEAP", "DEAR"]
    assert [e["cost_tier"] for e in ranked] == [0, 1]


def _cash(cents: int, currency: str = "USD") -> list[dict]:
    return [
        {
            "leg_role": "onward",
            "amount_cents": cents,
            "currency": currency,
            "duration_minutes": 180,
            "airline": "Japan Airlines",
        }
    ]


def test_cash_rides_as_its_own_pareto_dimension(biz_trip: str) -> None:
    # A cash hybrid and an all-award direct on a different program stay incomparable — cash is a
    # distinct axis, never fungible with miles — so both hold the front, neither pruned.
    direct = journey("DIRECT", {"united": 90000})
    hybrid = journey("HYBRID", {"aeroplan": 80000}, cash=_cash(40000))
    ranked = do_rank(biz_trip, [direct, hybrid])
    assert order(ranked) == ["HYBRID", "DIRECT"]  # tiebreak on miles; both on the front
    assert all(e["cost_tier"] == 0 for e in ranked)


def test_same_program_direct_dominates_costlier_cash_hybrid(biz_trip: str) -> None:
    # Same award miles but the hybrid also spends cash: the all-award direct strictly dominates, so
    # the hybrid sinks to a later tier — ranked lower, never dropped.
    direct = journey("DIRECT", {"united": 80000})
    hybrid = journey("HYBRID", {"united": 80000}, cash=_cash(12000))
    ranked = do_rank(biz_trip, [hybrid, direct])
    assert order(ranked) == ["DIRECT", "HYBRID"]
    assert [e["cost_tier"] for e in ranked] == [0, 1]


def test_split_same_currency_cash_legs_sum_onto_one_axis(biz_trip: str) -> None:
    # Two USD legs read as one summed $cash:USD axis, so the all-award direct still dominates.
    direct = journey("DIRECT", {"united": 80000})
    split = journey("SPLIT", {"united": 80000}, cash=_cash(7000) + _cash(5000))
    ranked = do_rank(biz_trip, [split, direct])
    assert order(ranked) == ["DIRECT", "SPLIT"]
    assert [e["cost_tier"] for e in ranked] == [0, 1]


def test_cross_currency_cash_axes_are_pareto_incomparable(biz_trip: str) -> None:
    # Distinct currency axes, no conversion: a collapsed cash axis would sink USD (40000 > 30000).
    usd = journey("USD", {"united": 80000}, cash=_cash(40000))
    eur = journey("EUR", {"united": 80000}, cash=_cash(30000, "EUR"))
    ranked = do_rank(biz_trip, [usd, eur])
    assert all(e["cost_tier"] == 0 for e in ranked)
    assert order(ranked) == ["EUR", "USD"]  # tiebreak: $cash:EUR sorts before $cash:USD


def test_funded_preferred_outranks_unfunded_soft_avoided(biz_trip: str) -> None:
    prefs.set_balance("united", 90000)
    unfunded = journey("UNFUNDED", {"delta": 80000}, soft=True, airlines="DL")
    funded = journey("FUNDED", {"united": 82000})
    assert order(do_rank(biz_trip, [unfunded, funded])) == ["FUNDED", "UNFUNDED"]


def test_soft_avoid_sinks_within_band(biz_trip: str) -> None:
    soft = journey("SOFT", {"united": 80000}, soft=True)
    clean = journey("CLEAN", {"united": 82000})
    assert order(do_rank(biz_trip, [soft, clean])) == ["CLEAN", "SOFT"]


def test_transfer_path_coverage_is_neutral_not_demote(biz_trip: str) -> None:
    prefs.set_balance("chase", 100000)  # covers a united shortfall 1:1, but not delta
    unfunded = journey("UNFUNDED", {"delta": 80000}, airlines="DL")
    transfer = journey("TRANSFER", {"united": 82000})
    assert order(do_rank(biz_trip, [unfunded, transfer])) == ["TRANSFER", "UNFUNDED"]


def test_status_earning_toward_goal_promotes(biz_trip: str) -> None:
    prefs.set_patch({"status_goals": [{"program": "united", "target": "1K", "by": "2026-12-31"}]})
    other = journey("OTHER", {"delta": 80000}, airlines="DL")
    goal = journey("GOAL", {"united": 82000})
    assert order(do_rank(biz_trip, [other, goal])) == ["GOAL", "OTHER"]


def test_matching_credit_promotes_when_retiered_secondary(biz_trip: str) -> None:
    prefs.instrument_add(monetary(issuer="united"))
    trips.set_patch(
        biz_trip, {"judgment": {"factors": {"trip_credits": {"priority": "secondary"}}}}
    )
    nocredit = journey("NOCREDIT", {"delta": 80000}, airlines="DL")
    credit = journey("CREDIT", {"united": 82000})
    assert order(do_rank(biz_trip, [nocredit, credit])) == ["CREDIT", "NOCREDIT"]


def test_deterministic_note_tier_never_reorders(biz_trip: str) -> None:
    prefs.instrument_add(monetary(issuer="united"))
    nocredit = journey("NOCREDIT", {"delta": 80000}, airlines="DL")
    credit = journey("CREDIT", {"united": 82000})
    # trip_credits stays note-tier by default: the promote annotates, never reorders past cheaper.
    assert order(do_rank(biz_trip, [nocredit, credit])) == ["NOCREDIT", "CREDIT"]


def test_seat_insufficient_gated_carries_to_dropped(biz_trip: str) -> None:
    gated = [{"journey_id": "COLLAPSED", "reason": "a leg's live seats are below the party"}]
    do_rank(biz_trip, [journey("KEPT", {"united": 82000})], gated=gated)
    dropped = rank_doc(biz_trip)["dropped"]
    assert [d["journey_id"] for d in dropped] == ["COLLAPSED"]


@pytest.mark.parametrize(
    ("constraints", "blocked_miles", "blocked_cabin", "blocked_departure", "reason"),
    [
        pytest.param(
            {"mileage_limit": {"miles": 120000}},
            130000,
            "J",
            "2026-09-07T09:00",
            "130000 miles over confirmed limit 120000",
            id="mileage-limit",
        ),
        pytest.param(
            {"cabin": {"value": "business", "confirmed": True}},
            95000,
            "W",
            "2026-09-07T09:00",
            "outbound award cabin W below confirmed cabin J",
            id="cabin-minimum",
        ),
        pytest.param(
            {"departure_days": {"days": ["Mon"], "confirmed": True}},
            95000,
            "J",
            "2026-09-08T09:00",
            "outbound departs Tue outside confirmed departure days ['Mon']",
            id="departure-day",
        ),
    ],
)
def test_hard_constraint_gate_set_discloses_drops(
    biz_trip: str,
    constraints: dict,
    blocked_miles: int,
    blocked_cabin: str,
    blocked_departure: str,
    reason: str,
) -> None:
    trips.set_patch(
        biz_trip,
        {"plan": {**PLAN, "constraints": constraints}},
    )
    blocked = journey(
        "BLOCKED",
        {"united": blocked_miles},
        cabin=blocked_cabin,
        departs_local=blocked_departure,
    )
    kept = journey("KEPT", {"united": 95000}, cabin="J", departs_local="2026-09-07T09:00")
    ranked = do_rank(biz_trip, [blocked, kept])
    assert order(ranked) == ["KEPT"]
    assert rank_doc(biz_trip)["dropped"] == [{"journey_id": "BLOCKED", "reason": reason}]


@pytest.mark.parametrize(
    ("candidate", "expected_order", "expected_dropped"),
    [
        pytest.param(
            journey(
                "MIXED-SEGMENT",
                {"united": 95000},
                award_legs=[
                    leg("outbound", "united", cabin="J", segment_cabins=("J", "Y"))
                ],
            ),
            [],
            [
                {
                    "journey_id": "MIXED-SEGMENT",
                    "reason": "outbound award cabin Y below confirmed cabin J",
                }
            ],
            id="mixed-segment-drops",
        ),
        pytest.param(
            journey(
                "GATEWAY-AWARD",
                {"united": 95000, "aeroplan": 25000},
                kind="gateway_award",
                award_legs=[
                    leg("outbound", "united", cabin="J", segment_cabins=("J",)),
                    leg("onward", "aeroplan", cabin="economy", segment_cabins=("Y",)),
                ],
            ),
            [],
            [
                {
                    "journey_id": "GATEWAY-AWARD",
                    "reason": "onward award cabin Y below confirmed cabin J",
                }
            ],
            id="gateway-award-economy-onward-drops",
        ),
        pytest.param(
            journey(
                "TWO-AWARD-LEGS",
                {"united": 95000, "aeroplan": 25000},
                kind="gateway_award",
                award_legs=[
                    leg("outbound", "united", cabin="MIXED", segment_cabins=("J", "F")),
                    leg("onward", "aeroplan", cabin="business", segment_cabins=("J",)),
                ],
            ),
            ["TWO-AWARD-LEGS"],
            [],
            id="all-award-segments-at-or-above-minimum-kept",
        ),
        pytest.param(
            journey(
                "CASH-ONWARD",
                {"united": 95000},
                cash=_cash(25000),
                cash_leg=cash_onward(),
                award_legs=[
                    leg("outbound", "united", cabin="J", segment_cabins=("J",))
                ],
            ),
            ["CASH-ONWARD"],
            [],
            id="cash-leg-exempt",
        ),
    ],
)
def test_cabin_constraint_checks_every_award_segment(
    biz_trip: str,
    candidate: dict,
    expected_order: list[str],
    expected_dropped: list[dict],
) -> None:
    trips.set_patch(
        biz_trip,
        {
            "plan": {
                **PLAN,
                "constraints": {"cabin": {"value": "business", "confirmed": True}},
            }
        },
    )
    assert order(do_rank(biz_trip, [candidate])) == expected_order
    assert rank_doc(biz_trip)["dropped"] == expected_dropped


def test_absent_constraints_do_not_gate_preference_misses(biz_trip: str) -> None:
    preference_miss = journey(
        "PREFERENCE-MISS",
        {"united": 95000},
        cabin="W",
        departs_local="2026-09-08T09:00",
    )
    assert order(do_rank(biz_trip, [preference_miss])) == ["PREFERENCE-MISS"]
    assert rank_doc(biz_trip)["dropped"] == []


def test_status_earning_fact_when_active(biz_trip: str) -> None:
    prefs.set_patch({"status_goals": [{"program": "united", "target": "1K", "by": "2026-12-31"}]})
    ranked = do_rank(biz_trip, [journey("A", {"united": 80000})])
    fact = ranked[0]["facts"]["status_earning"][0]
    assert fact["program"] == "united"
    assert fact["matches_goal"] is True
    assert fact["earns_on_redemption"] is True


def test_trip_credits_fact_matches_issuer_and_flags_expiry(biz_trip: str) -> None:
    prefs.instrument_add(
        monetary(issuer="united", expires="2026-08-01")
    )  # within 90d of frozen now
    prefs.instrument_add(monetary(issuer="delta", expires="2027-06-01"))  # far off + non-matching
    ranked = do_rank(biz_trip, [journey("A", {"united": 80000})])
    matches = ranked[0]["facts"]["trip_credits"]
    assert [m["issuer"] for m in matches] == ["united"]
    assert matches[0]["expiring"] is True


def test_expired_credit_never_matches(biz_trip: str) -> None:
    prefs.instrument_add(
        monetary(issuer="united", expires="2026-07-01")
    )  # expired before frozen now
    ranked = do_rank(biz_trip, [journey("A", {"united": 80000})])
    assert ranked[0]["facts"]["trip_credits"] == []


def test_notable_stretch_beyond_the_cut_is_surfaced(biz_trip: str) -> None:
    # Six cheap journeys fill the cut; a seventh, pricier one lands beyond it. Its seat_quality
    # promote can't cross the cost band, so it stays beyond the cut — and assess flags it a stretch.
    js = [journey(f"J{i}", {"united": 80000 + i * 1000}) for i in range(6)]
    js.append(journey("STRETCH", {"united": 200000}))
    assess = {"STRETCH": verdicts(("seat_quality", "promote"))}
    notable = [{"journey_id": "STRETCH", "why": "suites despite the later return"}]
    do_rank(biz_trip, js, assess=assess, notable=notable)
    doc = rank_doc(biz_trip)
    assert len(doc["ranked"]) == 7
    assert doc["ranked"][-1]["journey"]["id"] == "STRETCH"  # a promote can't lift it into the cut
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["STRETCH"]
    assert doc["notable_stretches"][0]["why"] == "suites despite the later return"


def test_notable_stretch_within_the_cut_is_not_duplicated(biz_trip: str) -> None:
    js = [journey(f"J{i}", {"united": 80000 + i * 1000}) for i in range(3)]
    notable = [{"journey_id": "J1", "why": "already shown"}]
    do_rank(biz_trip, js, assess={}, notable=notable)
    assert rank_doc(biz_trip)["notable_stretches"] == []  # J1 is within the cut, not a stretch


def assess_doc(journeys: dict | None = None, notable: list | None = None) -> str:
    return json.dumps({"journeys": journeys or {}, "notable_stretches": notable or []})


def test_assess_write_accepts_judgment_factors(biz_trip: str) -> None:
    doc = assess_doc(
        {
            "A": verdicts(("seat_quality", "promote"), ("layovers", "demote")),
            "B": {
                "verdicts": [
                    {
                        "factor": "cash_anomaly",
                        "leg": "outbound",
                        "verdict": "neutral",
                        "evidence": "typical",
                    }
                ]
            },
        },
        notable=[{"journey_id": "A", "why": "suites despite the later return"}],
    )
    trips.artifact_write(biz_trip, "assess.json", doc)
    assert json.loads(trips.artifact_read(biz_trip, "assess.json")) == json.loads(doc)


def test_assess_write_rejects_deterministic_factor(biz_trip: str) -> None:
    # Lead-B guard: a deterministic-lane verdict would double-count against _deterministic_verdicts.
    with pytest.raises(UsageError) as err:
        trips.artifact_write(
            biz_trip, "assess.json", assess_doc({"A": verdicts(("affordability", "demote"))})
        )
    assert "affordability" in str(err.value)


@pytest.mark.parametrize(
    "factor",
    ["airline_preference", "departure_days", "status_earning", "points_purchase", "trip_credits"],
)
def test_assess_write_rejects_every_deterministic_factor(biz_trip: str, factor: str) -> None:
    with pytest.raises(UsageError):
        trips.artifact_write(
            biz_trip, "assess.json", assess_doc({"A": verdicts((factor, "promote"))})
        )


def test_assess_write_rejects_unknown_factor(biz_trip: str) -> None:
    with pytest.raises(UsageError):
        trips.artifact_write(
            biz_trip, "assess.json", assess_doc({"A": verdicts(("made_up", "promote"))})
        )


def test_assess_write_rejects_bad_verdict_value(biz_trip: str) -> None:
    with pytest.raises(UsageError):
        trips.artifact_write(
            biz_trip, "assess.json", assess_doc({"A": verdicts(("layovers", "boost"))})
        )


def test_assess_write_rejects_missing_verdict_key(biz_trip: str) -> None:
    doc = assess_doc(
        {"A": {"verdicts": [{"factor": "layovers", "verdict": "promote", "evidence": "x"}]}}
    )
    with pytest.raises(UsageError):
        trips.artifact_write(biz_trip, "assess.json", doc)


def test_assess_write_rejects_extra_verdict_key(biz_trip: str) -> None:
    doc = assess_doc(
        {
            "A": {
                "verdicts": [
                    {
                        "factor": "layovers",
                        "leg": None,
                        "verdict": "promote",
                        "evidence": "x",
                        "weight": 3,
                    }
                ]
            }
        }
    )
    with pytest.raises(UsageError):
        trips.artifact_write(biz_trip, "assess.json", doc)


# ---- availability verification (enhance-verify.json rank-time fold) ------------------------


def verify_result(
    target_id: str,
    outcome: str,
    *,
    observed: dict | None = None,
    checked_at: str = "2026-07-13T14:32:00+00:00",
    method: str = "cookie",
    evidence: str = "live-site check",
) -> dict:
    return {
        "target_id": target_id,
        "outcome": outcome,
        "checked_at": checked_at,
        "method": method,
        "observed": observed,
        "evidence": evidence,
    }


def write_verify(slug: str, *rows: dict) -> None:
    doc = {"enhancer": "verify", "results": {r["target_id"]: r for r in rows}}
    trips.artifact_write(slug, "enhance-verify.json", json.dumps(doc))


def test_availability_verified_inactive_without_artifact(biz_trip: str) -> None:
    profile = factors.derive_profile(trips.show(biz_trip), prefs.show(), slug=biz_trip)
    assert profile["availability_verified"]["active"] is False
    entry = do_rank(biz_trip, [journey("A", {"united": 80000})])[0]
    assert "availability_verification" not in entry["facts"]  # no artifact → no fold


def test_availability_verified_active_with_artifact(biz_trip: str) -> None:
    write_verify(biz_trip, verify_result("outbound-united:J", "confirmed", observed={}))
    profile = factors.derive_profile(trips.show(biz_trip), prefs.show(), slug=biz_trip)
    assert profile["availability_verified"]["active"] is True


def test_verified_gone_yields_demote_verdict_and_fact(biz_trip: str) -> None:
    write_verify(biz_trip, verify_result("outbound-united:J", "gone"))
    entry = do_rank(biz_trip, [journey("A", {"united": 80000})])[0]
    assert entry["facts"]["availability_verification"] == [
        {
            "leg": "outbound",
            "availability_id": "outbound-united",
            "outcome": "gone",
            "checked_at": "2026-07-13T14:32:00+00:00",
            "observed": None,
            "evidence": "live-site check",
        }
    ]
    assert {
        "factor": "availability_verified",
        "leg": "outbound",
        "verdict": "demote",
    } in entry["verdicts"]


def test_verified_confirmed_yields_fact_but_no_verdict(biz_trip: str) -> None:
    write_verify(
        biz_trip, verify_result("outbound-united:J", "confirmed", observed={"remaining_seats": 4})
    )
    entry = do_rank(biz_trip, [journey("A", {"united": 80000})])[0]
    fact = entry["facts"]["availability_verification"][0]
    assert fact["outcome"] == "confirmed"
    assert fact["observed"] == {"remaining_seats": 4}
    # confirmed annotates only — a promote would make ordering depend on verifier reach.
    assert all(v["factor"] != "availability_verified" for v in entry["verdicts"])


def test_verified_degraded_yields_demote_verdict_and_fact(biz_trip: str) -> None:
    write_verify(
        biz_trip, verify_result("outbound-united:J", "degraded", observed={"remaining_seats": 1})
    )
    entry = do_rank(biz_trip, [journey("A", {"united": 80000})])[0]
    fact = entry["facts"]["availability_verification"][0]
    assert fact["outcome"] == "degraded"
    assert fact["observed"] == {"remaining_seats": 1}
    assert {
        "factor": "availability_verified",
        "leg": "outbound",
        "verdict": "demote",
    } in entry["verdicts"]


def test_verified_inconclusive_yields_fact_but_no_verdict(biz_trip: str) -> None:
    write_verify(biz_trip, verify_result("outbound-united:J", "inconclusive"))
    entry = do_rank(biz_trip, [journey("A", {"united": 80000})])[0]
    fact = entry["facts"]["availability_verification"][0]
    assert fact["outcome"] == "inconclusive"
    assert fact["observed"] is None
    # inconclusive annotates only — no signal strong enough to reorder.
    assert all(v["factor"] != "availability_verified" for v in entry["verdicts"])


def test_verified_gone_demotes_within_cost_band(biz_trip: str) -> None:
    prefs.set_balance("united", 90000)  # both covered → afford is not the differentiator
    prefs.set_balance("aeroplan", 90000)
    write_verify(biz_trip, verify_result("outbound-united:J", "gone"))
    gone = journey("A", {"united": 80000})  # outbound leg id outbound-united → verified gone
    clean = journey("B", {"aeroplan": 82000})  # no matching verify result
    # availability_verified is a primary lane verdict: the gone journey sinks below its band-mate.
    assert order(do_rank(biz_trip, [gone, clean])) == ["B", "A"]


def miss(code: str) -> dict:
    return {"code": code, "delta": 1, "annotation": f"{code} miss"}


def _entry(j: dict) -> dict:
    return {"journey": j}


def cabin_primary(slug: str, *, mileage: bool = False) -> None:
    # Declared preference priorities alone drive the primary lane — no judgment.factors crutch.
    preferences: dict = {"cabin": {"value": "business", "priority": "primary"}}
    if mileage:
        preferences["mileage_target"] = {
            "value": {"miles": 70000, "scope": "total"},
            "priority": "primary",
        }
    trips.set_patch(slug, {"plan": {**PLAN, "preferences": preferences}})


@pytest.mark.parametrize(
    ("a_misses", "b_misses", "dominates"),
    [
        pytest.param([miss("cabin")], [], False, id="cheaper-missing-never-dominates-clearing"),
        pytest.param([miss("cabin")], [miss("cabin")], True, id="both-missing-bands-as-before"),
        pytest.param([], [], True, id="both-clearing-guard-passes"),
        pytest.param([], [miss("cabin")], True, id="clearing-may-dominate-missing"),
    ],
)
def test_dominates_primary_clears_guard(
    a_misses: list[dict], b_misses: list[dict], dominates: bool
) -> None:
    a = _entry(journey("A", {"united": 60000}, misses=a_misses))
    b = _entry(journey("B", {"united": 90000}, misses=b_misses))
    assert factors._dominates(a, b, frozenset({"cabin"})) is dominates
    # With no primary codes the guard is inert: cheaper-beyond-band always dominates.
    assert factors._dominates(a, b, frozenset()) is True


@pytest.mark.parametrize(
    ("a_cost", "b_cost", "b_cash", "expected"),
    [
        pytest.param({"united": 60000}, {"united": 90000}, None, True, id="beyond-band"),
        pytest.param({"united": 80000}, {"united": 90000}, None, False, id="within-band"),
        pytest.param({"united": 100000}, {"united": 115000}, None, False, id="band-edge"),
        pytest.param({"united": 100000}, {"united": 115001}, None, True, id="past-band-edge"),
        pytest.param(
            {"united": 40000, "delta": 40000},
            {"united": 50000, "delta": 50000},
            None,
            True,
            id="mixed-pareto-dominated",
        ),
        pytest.param({"united": 90000}, {"delta": 80000}, None, False, id="cross-incomparable"),
        pytest.param({"united": 80000}, {"united": 80000}, _cash(12000), True, id="cash-axis"),
    ],
)
def test_dominates_with_empty_codes_matches_prior_behavior(
    a_cost: dict, b_cost: dict, b_cash: list | None, expected: bool
) -> None:
    a = _entry(journey("A", a_cost))
    b = _entry(journey("B", b_cost, cash=b_cash))
    assert factors._dominates(a, b, frozenset()) is expected


@pytest.mark.parametrize(
    ("cash", "expected"),
    [
        pytest.param(None, {"united": 80000}, id="cash-free-no-axis"),
        pytest.param(_cash(0), {"united": 80000}, id="zero-cash-no-axis"),
        pytest.param(
            _cash(7000) + _cash(-7000),
            {"united": 80000},
            id="same-currency-sums-to-zero-no-axis",
        ),
        pytest.param(_cash(12000), {"united": 80000, "$cash:USD": 12000}, id="single-component"),
        pytest.param(
            _cash(7000) + _cash(5000),
            {"united": 80000, "$cash:USD": 12000},
            id="same-currency-components-sum",
        ),
        pytest.param(
            _cash(12000) + _cash(30000, "EUR"),
            {"united": 80000, "$cash:USD": 12000, "$cash:EUR": 30000},
            id="one-axis-per-currency",
        ),
    ],
)
def test_cost_vector_keys_cash_per_currency(cash: list | None, expected: dict) -> None:
    assert factors._cost_vector(_entry(journey("J", {"united": 80000}, cash=cash))) == expected


def test_zero_cash_preserves_same_program_band_behavior(biz_trip: str) -> None:
    zero_cash = [
        journey("Z80", {"united": 80000}, cash=_cash(0)),
        journey("Z90", {"united": 90000}, cash=_cash(0)),
    ]
    cash_free = [
        journey("Z80", {"united": 80000}),
        journey("Z90", {"united": 90000}),
    ]
    zero_entries = [_entry(j) for j in zero_cash]
    cash_free_entries = [_entry(j) for j in cash_free]
    zero_dominance = [
        factors._dominates(zero_entries[0], zero_entries[1], frozenset()),
        factors._dominates(zero_entries[1], zero_entries[0], frozenset()),
    ]
    cash_free_dominance = [
        factors._dominates(cash_free_entries[0], cash_free_entries[1], frozenset()),
        factors._dominates(cash_free_entries[1], cash_free_entries[0], frozenset()),
    ]
    assert zero_dominance == cash_free_dominance == [False, False]

    zero_ranked = do_rank(biz_trip, zero_cash)
    cash_free_ranked = do_rank(biz_trip, cash_free)
    assert [e["cost_tier"] for e in zero_ranked] == [
        e["cost_tier"] for e in cash_free_ranked
    ] == [0, 0]
    assert order(zero_ranked) == order(cash_free_ranked) == ["Z80", "Z90"]


@pytest.mark.parametrize(
    ("a_cash", "b_cash", "expected"),
    [
        pytest.param(None, _cash(12000), True, id="cash-free-dominates-equal-miles-hybrid"),
        pytest.param(_cash(12000), None, False, id="cash-bearing-never-dominates-cash-free"),
        pytest.param(
            _cash(7000) + _cash(4999), _cash(12000), True, id="split-sum-cheaper-dominates"
        ),
        pytest.param(_cash(7000) + _cash(5000), _cash(12000), False, id="split-sum-equal-no-edge"),
        pytest.param(_cash(40000), _cash(30000, "EUR"), False, id="usd-never-dominates-eur"),
        pytest.param(_cash(30000, "EUR"), _cash(40000), False, id="eur-never-dominates-usd"),
    ],
)
def test_dominates_across_cash_axes(
    a_cash: list | None, b_cash: list | None, expected: bool
) -> None:
    a = _entry(journey("A", {"united": 80000}, cash=a_cash))
    b = _entry(journey("B", {"united": 80000}, cash=b_cash))
    assert factors._dominates(a, b, frozenset()) is expected


def test_guard_keeps_clearing_journey_on_the_front() -> None:
    def entries() -> list[dict]:
        return [
            _entry(journey("MISS", {"united": 60000}, misses=[miss("cabin")])),
            _entry(journey("CLEAR", {"united": 90000})),
        ]

    guarded = entries()
    factors._assign_cost_tiers(guarded, frozenset({"cabin"}))
    assert [e["_cost_tier"] for e in guarded] == [0, 0]  # both stay tier 0
    unguarded = entries()
    factors._assign_cost_tiers(unguarded, frozenset())
    assert [e["_cost_tier"] for e in unguarded] == [0, 1]  # without the guard, CLEAR sinks


def _random_entry(rng: random.Random, jid: str) -> dict:
    # Mixed band/Pareto/cash/clears shapes across the band edges.
    programs = rng.sample(["united", "delta", "aeroplan", "avianca"], rng.randint(1, 2))
    miles = [40000, 60000, 80000, 90000, 100000, 115000, 115001, 200000]
    by_program = {p: rng.choice(miles) for p in programs}
    cash = _cash(rng.choice([0, 12000, 40000])) if rng.random() < 0.4 else None
    misses = [miss("cabin")] if rng.random() < 0.4 else []
    return _entry(journey(jid, by_program, cash=cash, misses=misses))


def test_assign_cost_tiers_terminates_over_random_populations() -> None:
    # Property sweep: the empty-front tripwire never fires; every entry lands in a contiguous tier.
    rng = random.Random(0xC057)
    for pop in range(200):
        codes = frozenset({"cabin"}) if rng.random() < 0.5 else frozenset()
        entries = [_random_entry(rng, f"J{i}") for i in range(rng.randint(1, 8))]
        factors._assign_cost_tiers(entries, codes)  # raises AssertionError if a front is ever empty
        tiers = [e["_cost_tier"] for e in entries]
        assert all(isinstance(t, int) for t in tiers), f"population {pop} left an entry untiered"
        assert set(tiers) == set(range(max(tiers) + 1)), f"population {pop} skipped a tier"


def test_assign_cost_tiers_reverts_on_mutual_domination() -> None:
    # Equal negative totals dominate each other, emptying the front; the seeded sweep never
    # generates negatives, so only this guards silent-collapse code that returns tiers.
    pair = [_entry(journey(jid, {"united": -100})) for jid in ("A", "B")]
    with pytest.raises(AssertionError):
        factors._assign_cost_tiers(pair, frozenset())


def test_assign_cost_tiers_dominance_strictly_lowers_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    # Metamorphic wrap: every True domination must strictly lower the L1 cost sum, else a future
    # _dominates edit letting equal-cost entries dominate reintroduces cycle potential.
    original = factors._dominates_cleared

    def checked(a: dict, b: dict, a_clears: frozenset[str], b_clears: frozenset[str]) -> bool:
        result = original(a, b, a_clears, b_clears)
        if result:
            assert sum(factors._cost_vector(a).values()) < sum(factors._cost_vector(b).values())
        return result

    monkeypatch.setattr(factors, "_dominates_cleared", checked)
    rng = random.Random(0xF00D)
    for _ in range(200):
        codes = frozenset({"cabin"}) if rng.random() < 0.5 else frozenset()
        entries = [_random_entry(rng, f"J{i}") for i in range(rng.randint(1, 8))]
        factors._assign_cost_tiers(entries, codes)


def _single_cash_axis_cost_vector(entry: dict) -> dict[str, int]:
    # The pre-per-currency vector: every cash component summed onto one "$cash" axis.
    journey = entry["journey"]
    vector = dict(journey["cost"]["mileage"]["by_program"])
    cash_cents = sum(component["amount_cents"] for component in journey["cost"]["cash"])
    if cash_cents:
        vector["$cash"] = cash_cents
    return vector


def test_single_currency_populations_bit_identical_to_single_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Equivalence sweep: on single-currency shapes (all of today's fixtures), per-currency axes
    # reproduce the single-axis vector's dominance, tier assignments, and order bit-for-bit.
    rng = random.Random(0x2B2B)
    for pop in range(5000):
        codes = frozenset({"cabin"}) if rng.random() < 0.5 else frozenset()
        entries = [_random_entry(rng, f"J{i}") for i in range(rng.randint(1, 8))]
        for entry in entries:
            entry["verdicts"] = []
        dominance = [
            factors._dominates(a, b, codes) for a in entries for b in entries if a is not b
        ]
        ranked = order(factors._order(entries, {}, set(), codes))
        tiers = [e["_cost_tier"] for e in entries]
        with monkeypatch.context() as m:
            m.setattr(factors, "_cost_vector", _single_cash_axis_cost_vector)
            head_dominance = [
                factors._dominates(a, b, codes) for a in entries for b in entries if a is not b
            ]
            head_ranked = order(factors._order(entries, {}, set(), codes))
            head_tiers = [e["_cost_tier"] for e in entries]
        assert dominance == head_dominance, f"population {pop} dominance diverged"
        assert tiers == head_tiers, f"population {pop} tiers diverged"
        assert ranked == head_ranked, f"population {pop} order diverged"


@pytest.mark.parametrize(
    ("costs", "expected_tiers"),
    [
        pytest.param(
            [("A", {"united": 80000}), ("B", {"united": 90000}), ("C", {"united": 200000})],
            {"A": 0, "B": 0, "C": 1},
            id="banded-plus-dominated",
        ),
        pytest.param(
            [("A", {"united": 100000}), ("B", {"united": 115000})],
            {"A": 0, "B": 0},
            id="band-edge-same-tier",
        ),
        pytest.param(
            [("A", {"united": 100000}), ("B", {"united": 115001})],
            {"A": 0, "B": 1},
            id="past-band-edge-later-tier",
        ),
        pytest.param(
            [("U", {"united": 90000}), ("D", {"delta": 80000})],
            {"U": 0, "D": 0},
            id="cross-program-front",
        ),
        pytest.param(
            [
                ("CHEAP", {"united": 40000, "delta": 40000}),
                ("DEAR", {"united": 50000, "delta": 50000}),
            ],
            {"CHEAP": 0, "DEAR": 1},
            id="mixed-dominated",
        ),
    ],
)
def test_no_primary_preference_tier_assignment_pinned(
    biz_trip: str, costs: list[tuple[str, dict]], expected_tiers: dict[str, int]
) -> None:
    ranked = do_rank(biz_trip, [journey(jid, cost) for jid, cost in costs])
    assert {e["journey"]["id"]: e["cost_tier"] for e in ranked} == expected_tiers


def test_all_economy_board_regression(biz_trip: str) -> None:
    cabin_primary(biz_trip)
    economy = [
        journey(f"E{i}", {"united": 60000 + i * 1000}, cabin="Y", misses=[miss("cabin")])
        for i in range(7)
    ]
    js = [*economy, journey("BIZ", {"united": 90000})]

    # Without assess.json: the guard keeps BIZ tier 0 but the tiebreak leaves it beyond the
    # cut; the deterministic trigger puts it on the board, naming the cabin preference.
    do_rank(biz_trip, js)
    doc = rank_doc(biz_trip)
    assert [e["journey"]["id"] for e in doc["ranked"][:6]] == ["E0", "E1", "E2", "E3", "E4", "E5"]
    assert all(e["cost_tier"] == 0 for e in doc["ranked"])  # guard: no economy buries BIZ
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["BIZ"]
    assert doc["notable_stretches"][0]["why"] == (
        "clears your business cabin preference — every finalist misses it"
    )

    # With assess verdicts: the guard keeps BIZ tier 0 and the primary lane lifts it to finalist.
    assess = {f"E{i}": verdicts(("cabin_fit", "demote")) for i in range(7)}
    assess["BIZ"] = verdicts(("cabin_fit", "promote"))
    do_rank(biz_trip, js, assess=assess)
    doc = rank_doc(biz_trip)
    assert [e["journey"]["id"] for e in doc["ranked"][:6]] == [
        "BIZ",
        "E0",
        "E1",
        "E2",
        "E3",
        "E4",
    ]
    assert doc["ranked"][0]["cost_tier"] == 0
    assert doc["notable_stretches"] == []  # BIZ is a finalist clearing cabin — no trigger


def test_trigger_dedupes_across_codes(biz_trip: str) -> None:
    cabin_primary(biz_trip, mileage=True)
    finalists = [
        journey(
            f"F{i}",
            {"united": 60000 + i * 1000},
            misses=[miss("cabin"), miss("mileage_target")],
        )
        for i in range(6)
    ]
    do_rank(biz_trip, [*finalists, journey("BOTH", {"united": 90000})])
    doc = rank_doc(biz_trip)
    # BOTH clears both primary codes but surfaces once, under the first code.
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["BOTH"]
    assert doc["notable_stretches"][0]["why"] == (
        "clears your business cabin preference — every finalist misses it"
    )


def test_trigger_surfaces_one_stretch_per_code(biz_trip: str) -> None:
    cabin_primary(biz_trip, mileage=True)
    finalists = [
        journey(
            f"F{i}",
            {"united": 60000 + i * 1000},
            misses=[miss("cabin"), miss("mileage_target")],
        )
        for i in range(6)
    ]
    cabin_clear = journey("CABINCLEAR", {"united": 90000}, misses=[miss("mileage_target")])
    mileage_clear = journey("MILEAGECLEAR", {"united": 95000}, misses=[miss("cabin")])
    do_rank(biz_trip, [*finalists, cabin_clear, mileage_clear])
    doc = rank_doc(biz_trip)
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["CABINCLEAR", "MILEAGECLEAR"]
    assert doc["notable_stretches"][1]["why"] == (
        "clears your mileage target preference — every finalist misses it"
    )


def test_trigger_dedupes_against_assess_picks(biz_trip: str) -> None:
    cabin_primary(biz_trip)
    finalists = [
        journey(f"F{i}", {"united": 60000 + i * 1000}, misses=[miss("cabin")]) for i in range(6)
    ]
    stretch = journey("STRETCH", {"united": 90000})
    notable = [{"journey_id": "STRETCH", "why": "assess picked it"}]
    do_rank(biz_trip, [*finalists, stretch], assess={}, notable=notable)
    doc = rank_doc(biz_trip)
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["STRETCH"]
    assert doc["notable_stretches"][0]["why"] == "assess picked it"  # the assess why wins


def test_no_trigger_when_a_finalist_clears_the_code(biz_trip: str) -> None:
    cabin_primary(biz_trip)
    clear = journey("CLEAR", {"united": 60000})
    finalists = [
        journey(f"F{i}", {"united": 61000 + i * 1000}, misses=[miss("cabin")]) for i in range(5)
    ]
    do_rank(biz_trip, [clear, *finalists, journey("BEYOND", {"united": 90000})])
    doc = rank_doc(biz_trip)
    assert [e["journey"]["id"] for e in doc["ranked"][:6]] == [
        "CLEAR",
        "F0",
        "F1",
        "F2",
        "F3",
        "F4",
    ]
    assert doc["notable_stretches"] == []


def test_mixed_window_declaration_guards_and_triggers_only_the_primary_code(
    biz_trip: str,
) -> None:
    preferences = {
        "outbound_departure_window": {"value": WINDOW, "priority": "primary"},
        "return_arrival_by": {"value": {"latest_local_date": "2026-09-20"}, "priority": "note"},
    }
    trips.set_patch(biz_trip, {"plan": {**PLAN, "preferences": preferences}})
    finalists = [
        journey(
            f"F{i}",
            {"united": 60000 + i * 1000},
            misses=[miss("outbound_departure_window"), miss("return_arrival_by")],
        )
        for i in range(6)
    ]
    ret_clear = journey("RETCLEAR", {"united": 90000}, misses=[miss("outbound_departure_window")])
    ob_clear = journey("OBCLEAR", {"united": 95000}, misses=[miss("return_arrival_by")])
    do_rank(biz_trip, [*finalists, ret_clear, ob_clear])
    doc = rank_doc(biz_trip)
    tiers = {e["journey"]["id"]: e["cost_tier"] for e in doc["ranked"]}
    assert tiers["OBCLEAR"] == 0  # the declared-primary outbound code guards it
    assert tiers["RETCLEAR"] == 1  # the note-declared return code no longer guards
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["OBCLEAR"]
    assert doc["notable_stretches"][0]["why"] == (
        "clears your outbound departure window preference — every finalist misses it"
    )


def test_trigger_skips_code_an_assess_pick_already_clears(biz_trip: str) -> None:
    cabin_primary(biz_trip)
    finalists = [
        journey(f"F{i}", {"united": 60000 + i * 1000}, misses=[miss("cabin")]) for i in range(6)
    ]
    picked = journey("PICKED", {"united": 90000})
    also_clears = journey("ALSO", {"united": 95000})
    notable = [{"journey_id": "PICKED", "why": "assess picked it"}]
    do_rank(biz_trip, [*finalists, picked, also_clears], assess={}, notable=notable)
    doc = rank_doc(biz_trip)
    # PICKED already clears cabin — no second, worse stretch surfaces for the same code.
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["PICKED"]
    assert doc["notable_stretches"][0]["why"] == "assess picked it"


def test_trigger_skips_a_verified_gone_stretch(biz_trip: str) -> None:
    cabin_primary(biz_trip)
    write_verify(biz_trip, verify_result("outbound-united-gone:J", "gone"))
    finalists = [
        journey(f"F{i}", {"united": 60000 + i * 1000}, misses=[miss("cabin")]) for i in range(6)
    ]
    gone = journey(
        "GONE",
        {"united": 90000},
        award_legs=[{**leg("outbound", "united"), "id": "outbound-united-gone"}],
    )
    live = journey("LIVE", {"united": 115001})
    do_rank(biz_trip, [*finalists, gone, live])
    doc = rank_doc(biz_trip)
    tiers = {e["journey"]["id"]: e["cost_tier"] for e in doc["ranked"]}
    assert (tiers["GONE"], tiers["LIVE"]) == (0, 1)  # the gone journey outranks live on cost
    assert [e["journey"]["id"] for e in doc["ranked"][6:]] == ["GONE", "LIVE"]
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["LIVE"]
