import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from getaway import factors, paths, prefs, trips
from getaway.store import connect

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def _new(getaway_home: Path, plan: dict) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "party": 1,
            "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10},
            "plan": plan,
        },
    )
    return SLUG


DIRECT_PLAN = {
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
    "max_finalists": 6,
}
HYBRID_SPEC = {"gateways": ["NRT", "ICN"], "onward_dests": ["OKA", "KIX"], "max_hybrids": 4}
HYBRID_PLAN = {
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
    "hybrid": HYBRID_SPEC,
    "max_finalists": 6,
}


def rank_entry(cid: str, mileage: int, source: str = "united") -> dict:
    candidate = {
        "id": cid,
        "date": "2026-09-05",
        "origin": "SFO",
        "dest": "NRT",
        "source": source,
        "mileage": mileage,
        "seats": 2,
        "airlines": "UA",
        "direct": True,
        "soft": False,
        "departure_day_match": False,
    }
    return {
        "candidate": candidate,
        "factors": {"seat_quality": {"verdict": "promote"}},
        "facts": {"note": cid},
    }


def gw_cand(cid: str, dest: str, mileage: int) -> dict:
    return {
        "id": cid,
        "date": "2026-09-05",
        "origin": "SFO",
        "dest": dest,
        "source": "aeroplan",
        "mileage": mileage,
        "seats": 2,
        "airlines": "NH",
        "direct": True,
        "soft": False,
        "departure_day_match": False,
    }


def minima(gateway: str, dest: str, cabin: str, mileage: int) -> dict:
    return {
        "gateway": gateway,
        "onward_dest": dest,
        "cabin": cabin,
        "id": f"OW-{gateway}-{dest}",
        "date": "2026-09-08",
        "source": "aeroplan",
        "mileage": mileage,
        "seats": 2,
        "airlines": "NH",
        "direct": True,
    }


def quote(gateway: str, dest: str, cabin: str, price: float) -> dict:
    return {
        "gateway": gateway,
        "onward_dest": dest,
        "cabin": cabin,
        "price": price,
        "currency": "USD",
        "duration_minutes": 180,
        "stops": 0,
        "airline": "NH",
        "flight_number": "NH1",
    }


def write(slug: str, name: str, obj: object) -> None:
    trips.artifact_write(slug, name, json.dumps(obj))


def write_rank(slug: str, entries: list[dict]) -> None:
    write(slug, "rank.json", {"ranked": entries, "dropped": []})


def test_hybrid_absent_output_is_directs_only(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    write_rank(slug, [rank_entry("A", 80000), rank_entry("B", 90000)])
    doc = factors.finalize(slug, now=clock())
    assert doc["hybrids"] == []
    assert [d["candidate"]["id"] for d in doc["directs"]] == ["A", "B"]
    first = doc["directs"][0]
    assert first["kind"] == "direct"
    assert first["factors"] == {"seat_quality": {"verdict": "promote"}}
    assert first["facts"] == {"note": "A"}
    assert first["detail"] is None


def test_finalize_attaches_trip_detail_from_cache(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    detail = {"id": "A", "mileage": 80000, "booking_links": [{"label": "book", "primary": True}]}
    connect(paths.cache_db(), now=clock()).trip_detail_put("A", detail)
    write_rank(slug, [rank_entry("A", 80000)])
    doc = factors.finalize(slug, now=clock())
    assert doc["directs"][0]["detail"] == detail


def bridge_pair(gateway: str, dest: str, date: str = "2026-09-08") -> dict:
    return {"gateway": gateway, "onward_dest": dest, "date": date, "cash_cutoff_minutes": 240}


@pytest.fixture
def hybrid(getaway_home: Path) -> str:
    slug = _new(getaway_home, HYBRID_PLAN)
    write_rank(slug, [rank_entry("D1", 45000)])
    write(slug, "shortlist-gateway.json", {"candidates": [gw_cand("GW-NRT", "NRT", 80000),
                                                          gw_cand("GW-ICN", "ICN", 90000)],
                                           "considered": 2})
    write(
        slug,
        "onward.json",
        {
            "minima": [
                minima("NRT", "OKA", "economy", 30000),
                minima("ICN", "KIX", "economy", 25000),
            ],
            "bridge_pairs": [
                bridge_pair("NRT", "OKA"),
                bridge_pair("NRT", "KIX"),
                bridge_pair("ICN", "OKA"),
                bridge_pair("ICN", "KIX"),
            ],
        },
    )
    return slug


def kinds(doc: dict) -> list[tuple[str, str, str]]:
    return [(h["kind"], h["gateway"], h["onward_dest"]) for h in doc["hybrids"]]


def test_gateway_cash_and_two_award_compose_and_rank(hybrid: str) -> None:
    write(hybrid, "bridge.json", {"quotes": [quote("NRT", "OKA", "economy", 120.0),
                                             quote("ICN", "KIX", "economy", 150.0)]})
    doc = factors.finalize(hybrid, now=clock())
    # Ranked by total miles then cash: gateway-cash legs (award only) precede two-award stitches.
    assert kinds(doc) == [
        ("gateway-cash", "NRT", "OKA"),
        ("gateway-cash", "ICN", "KIX"),
        ("two-award", "NRT", "OKA"),
        ("two-award", "ICN", "KIX"),
    ]
    gc = doc["hybrids"][0]
    assert gc["award"]["id"] == "GW-NRT"
    assert gc["onward"] == {"mode": "cash", **quote("NRT", "OKA", "economy", 120.0)}
    ta = doc["hybrids"][2]
    assert ta["onward"]["mode"] == "award"
    assert ta["onward"]["mileage"] == 30000


def test_two_award_join_requires_pair_date_match(hybrid: str) -> None:
    # The onward minima hold a different date than the bridge pair — a date mismatch never
    # stitches; onward.json carries gateway-compatible dates and the join is keyed on them.
    write(
        hybrid,
        "onward.json",
        {
            "minima": [{**minima("NRT", "OKA", "economy", 30000), "date": "2026-09-12"}],
            "bridge_pairs": [bridge_pair("NRT", "OKA")],
        },
    )
    write(hybrid, "bridge.json", {"quotes": [quote("NRT", "OKA", "economy", 120.0)]})
    doc = factors.finalize(hybrid, now=clock())
    assert kinds(doc) == [("gateway-cash", "NRT", "OKA")]


def test_two_award_only_when_bridge_cabin_has_matching_minima(hybrid: str) -> None:
    # Bridge picks business past the cutoff, but the onward minima only holds economy → no stitch.
    write(hybrid, "bridge.json", {"quotes": [quote("NRT", "OKA", "business", 400.0)]})
    doc = factors.finalize(hybrid, now=clock())
    assert kinds(doc) == [("gateway-cash", "NRT", "OKA")]


def test_never_prunes_hybrid_by_cost(hybrid: str) -> None:
    # A cheap direct (45k) and an expensive stitch (80k + 30k) coexist — no cross-cost pruning.
    write(hybrid, "bridge.json", {"quotes": [quote("NRT", "OKA", "economy", 120.0)]})
    doc = factors.finalize(hybrid, now=clock())
    assert [d["candidate"]["id"] for d in doc["directs"]] == ["D1"]
    assert ("two-award", "NRT", "OKA") in kinds(doc)


def test_max_hybrids_caps_after_ranking(hybrid: str) -> None:
    plan = {**HYBRID_PLAN, "hybrid": {**HYBRID_SPEC, "max_hybrids": 2}}
    trips.set_patch(hybrid, {"plan": plan})
    write(hybrid, "bridge.json", {"quotes": [quote("NRT", "OKA", "economy", 120.0),
                                             quote("ICN", "KIX", "economy", 150.0)]})
    doc = factors.finalize(hybrid, now=clock())
    assert kinds(doc) == [("gateway-cash", "NRT", "OKA"), ("gateway-cash", "ICN", "KIX")]
