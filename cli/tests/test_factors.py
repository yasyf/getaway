import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import expand_doc

from getaway import factors, prefs, trips
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


def leg(role: str, source: str, *, soft: bool = False, airlines: str = "UA") -> dict:
    return {
        "role": role,
        "id": f"{role}-{source}",
        "cabin": "J",
        "source": source,
        "mode": "award",
        "soft": soft,
        "airlines": airlines,
        "detail": {},
        "fetched_at": None,
    }


def journey(
    jid: str,
    by_program: dict[str, int],
    *,
    soft: bool = False,
    airlines: str = "UA",
    cash: list | None = None,
) -> dict:
    single = len(by_program) == 1
    legs = [leg("outbound", src, soft=soft, airlines=airlines) for src in by_program]
    return {
        "id": jid,
        "kind": "round_trip",
        "legs": legs,
        "fit_facts": {},
        "preference_misses": [],
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


def _cash(cents: int) -> list[dict]:
    return [
        {
            "leg_role": "onward",
            "amount_cents": cents,
            "currency": "USD",
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


def test_mileage_limit_is_the_only_hard_budget(biz_trip: str) -> None:
    trips.set_patch(
        biz_trip, {"plan": {**PLAN, "constraints": {"mileage_limit": {"miles": 120000}}}}
    )
    blown = journey("BLOWN", {"united": 130000})
    kept = journey("KEPT", {"united": 95000})
    ranked = do_rank(biz_trip, [blown, kept])
    assert order(ranked) == ["KEPT"]
    dropped = rank_doc(biz_trip)["dropped"]
    assert dropped[0]["journey_id"] == "BLOWN"
    assert "over confirmed limit" in dropped[0]["reason"]


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
