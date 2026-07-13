import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from getaway import factors, prefs, trips

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
        "credits": [],
        "balances": {"programs": {}, "transferable": {}},
    }
    doc.update(over)
    return doc


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
        pytest.param("return_viability", {"plan": {"round_trip": True}}, {}, True, id="return-on"),
        pytest.param(
            "return_viability", {"plan": {"round_trip": False}}, {}, False, id="return-off"
        ),
        pytest.param(
            "status_earning",
            {},
            {"status_goals": [{"program": "delta", "target": "Platinum", "by": "2026-12-31"}]},
            True,
            id="status-on",
        ),
        pytest.param("status_earning", {}, {}, False, id="status-off"),
        pytest.param(
            "trip_credits",
            {},
            {"credits": [{"id": "a1", "issuer": "United", "amount": 200, "currency": "USD",
                          "expires": "2026-12-01"}]},
            True,
            id="credits-on",
        ),
        pytest.param("trip_credits", {}, {}, False, id="credits-off"),
        pytest.param(
            "points_purchase",
            {},
            {"balances": {"programs": {"united": 50000}, "transferable": {}}},
            True,
            id="purchase-on-balances",
        ),
        pytest.param("points_purchase", {}, {}, False, id="purchase-off-no-balances"),
        pytest.param(
            "seat_quality", {"cabin": "economy"}, {}, False, id="seat-quality-economy-off"
        ),
        pytest.param("cash_anomaly", {"cabin": "first"}, {}, True, id="cash-anomaly-first-on"),
    ],
)
def test_activation_matrix(fid: str, trip_over: dict, prefs_over: dict, active: bool) -> None:
    trip = {"cabin": "business", "vibe": [], "plan": {}, "judgment": {}}
    trip.update(trip_over)
    profile = factors.derive_profile(trip, empty_prefs(**prefs_over), slug=None)
    assert profile[fid]["active"] is active


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
            "plan": {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}],
                     "max_finalists": 6},
        },
    )
    return SLUG


def cand(cid: str, mileage: int, **over: object) -> dict:
    row = {
        "id": cid,
        "date": "2026-09-05",
        "origin": "SFO",
        "dest": "NRT",
        "source": "united",
        "mileage": mileage,
        "seats": 2,
        "airlines": "UA",
        "direct": True,
        "soft": False,
        "departure_day_match": False,
    }
    row.update(over)
    return row


def do_rank(
    slug: str,
    candidates: list[dict],
    assess: dict | None = None,
    expand: dict | None = None,
    max_finalists: int = 6,
) -> list[dict]:
    plan = {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}],
            "max_finalists": max_finalists}
    trips.set_patch(slug, {"plan": plan})
    shortlist_doc = {"candidates": candidates, "considered": len(candidates)}
    trips.artifact_write(slug, "shortlist.json", json.dumps(shortlist_doc))
    if assess is not None:
        trips.artifact_write(slug, "assess.json", json.dumps(assess))
    if expand is not None:
        trips.artifact_write(slug, "expand.json", json.dumps(expand))
    return factors.rank(slug, now=clock())


def order(ranked: list[dict]) -> list[str]:
    return [e["candidate"]["id"] for e in ranked]


def test_afford_annotates_never_gates(biz_trip: str) -> None:
    prefs.set_balance("united", 10000)  # far short of the award
    ranked = do_rank(biz_trip, [cand("POOR", 80000)])
    assert order(ranked) == ["POOR"]  # unaffordable, still ranked
    afford = ranked[0]["facts"]["afford"]
    assert afford["covered"] is False
    assert afford["shortfall"] == 70000


def test_barely_demotes_within_band_only(biz_trip: str) -> None:
    candidates = [cand("A", 80000), cand("B", 78000), cand("C", 200000)]
    expand = {"B": {"product": "barely", "mileage": 78000},
              "C": {"product": "barely", "mileage": 200000}}
    ranked = do_rank(biz_trip, candidates, expand=expand)
    # B (barely) demotes below A inside the shared band; C (barely) stays in its own far band.
    assert order(ranked) == ["A", "B", "C"]


def test_note_tier_never_reorders(biz_trip: str) -> None:
    candidates = [cand("A", 78000), cand("B", 80000)]
    # cash_anomaly is a note-tier factor; a demote on the cheaper A must not reorder.
    assess = {"A": {"cash_anomaly": {"verdict": "demote", "evidence": "pricey"}},
              "B": {"cash_anomaly": {"verdict": "promote", "evidence": "cheap"}}}
    ranked = do_rank(biz_trip, candidates, assess=assess)
    assert order(ranked) == ["A", "B"]


def test_primary_verdict_reorders_within_band(biz_trip: str) -> None:
    candidates = [cand("A", 80000), cand("B", 82000)]
    assess = {"A": {"seat_quality": {"verdict": "demote", "evidence": "dated"}},
              "B": {"seat_quality": {"verdict": "promote", "evidence": "suite"}}}
    ranked = do_rank(biz_trip, candidates, assess=assess)
    assert order(ranked) == ["B", "A"]


def test_secondary_breaks_primary_ties(biz_trip: str) -> None:
    candidates = [cand("A", 80000), cand("B", 82000)]
    # layovers is secondary; with no primary verdicts, it decides order within the band.
    assess = {"A": {"layovers": {"verdict": "demote", "evidence": "long"}},
              "B": {"layovers": {"verdict": "promote", "evidence": "nonstop"}}}
    ranked = do_rank(biz_trip, candidates, assess=assess)
    assert order(ranked) == ["B", "A"]


def test_ranking_currency_uses_bookable_mileage(biz_trip: str) -> None:
    # The sweep teaser said TEASER=50k, SAVER=80k; expansion books TEASER=80k, SAVER=52k.
    # Band placement, order, and the facts mileage must follow the bookable expanded numbers,
    # never the stale sweep row: a teaser price must not band beside a live booking price.
    candidates = [cand("TEASER", 50000), cand("SAVER", 80000)]
    expand = {"TEASER": {"mileage": 80000}, "SAVER": {"mileage": 52000}}
    ranked = do_rank(biz_trip, candidates, expand=expand)
    assert order(ranked) == ["SAVER", "TEASER"]
    saver, teaser = ranked
    assert saver["candidate"]["mileage"] == 52000
    assert saver["candidate"]["sweep_mileage"] == 80000
    assert saver["facts"]["afford"]["miles_needed"] == 52000
    assert saver["facts"]["afford"]["shortfall"] == 52000
    assert teaser["candidate"]["mileage"] == 80000
    assert teaser["candidate"]["sweep_mileage"] == 50000
    assert teaser["facts"]["afford"]["shortfall"] == 80000


def test_unexpanded_candidate_ranks_on_shortlist_mileage(biz_trip: str) -> None:
    # Quota-low trimmed the Expand phase for UNEXPANDED — no expand record — so it ranks on its
    # shortlist mileage (the defined currency for unexpanded candidates, not a fallback). EXPANDED
    # booked far under its teaser, so its bookable mileage ranks it first despite the higher sweep.
    candidates = [cand("EXPANDED", 100000), cand("UNEXPANDED", 70000)]
    expand = {"EXPANDED": {"mileage": 55000}}
    ranked = do_rank(biz_trip, candidates, expand=expand)
    assert order(ranked) == ["EXPANDED", "UNEXPANDED"]
    exp, unexp = ranked
    assert exp["candidate"]["mileage"] == 55000
    assert exp["candidate"]["sweep_mileage"] == 100000
    assert exp["facts"]["afford"]["shortfall"] == 55000
    assert "sweep_mileage" not in unexp["candidate"]  # ranked on its shortlist mileage, no drift
    assert unexp["candidate"]["mileage"] == 70000
    assert unexp["facts"]["afford"]["shortfall"] == 70000


def test_verdict_never_crosses_band(biz_trip: str) -> None:
    candidates = [cand("A", 80000), cand("B", 200000)]
    assess = {"B": {"seat_quality": {"verdict": "promote", "evidence": "suite"}}}
    ranked = do_rank(biz_trip, candidates, assess=assess)
    assert order(ranked) == ["A", "B"]  # B's promote can't cross the mileage band


def test_truncation_after_verdicts(biz_trip: str) -> None:
    candidates = [cand("A", 80000), cand("B", 82000)]
    assess = {"A": {"seat_quality": {"verdict": "demote", "evidence": "dated"}},
              "B": {"seat_quality": {"verdict": "promote", "evidence": "suite"}}}
    ranked = do_rank(biz_trip, candidates, assess=assess, max_finalists=1)
    assert order(ranked) == ["B"]  # promoted pricier B kept over cheaper A


def test_status_earning_fact_when_active(biz_trip: str) -> None:
    prefs.set_patch(
        {"status_goals": [{"program": "united", "target": "1K", "by": "2026-12-31"}]}
    )
    ranked = do_rank(biz_trip, [cand("A", 80000, source="united")])
    fact = ranked[0]["facts"]["status_earning"]
    assert fact["program"] == "united"
    assert fact["matches_goal"] is True
    assert fact["earns_on_redemption"] is True


def test_trip_credits_fact_matches_issuer_and_flags_expiry(biz_trip: str) -> None:
    prefs.credit_add("voucher", "united", 250, "USD", "2026-08-01")  # within 90d of frozen now
    prefs.credit_add("credit", "delta", 300, "USD", "2027-06-01")  # far off + non-matching
    ranked = do_rank(biz_trip, [cand("A", 80000, source="united")])
    matches = ranked[0]["facts"]["trip_credits"]
    assert [m["issuer"] for m in matches] == ["united"]
    assert matches[0]["expiring"] is True
