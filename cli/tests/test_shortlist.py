import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import api_row, seed

from getaway import prefs, shortlist, trips

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
DESTS = ["NRT", "HND", "BKK", "ICN", "SIN", "KIX", "OKA"]


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def set_plan(slug: str, **extra: object) -> None:
    plan: dict = {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": DESTS}]}
    plan["max_finalists"] = 6
    plan.update(extra)
    trips.set_patch(slug, {"plan": plan})


@pytest.fixture
def base(getaway_home: Path) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "party": 1,
            "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10},
        },
    )
    set_plan(SLUG)
    return SLUG


def biz(mileage: str, seats: int = 2, airlines: str = "UA", direct: bool = True) -> dict:
    return {"J": {"mileage": mileage, "seats": seats, "airlines": airlines, "direct": direct}}


def run(slug: str, rows: list[dict], gateway: bool = False, label: str = "asia") -> dict:
    seed(slug, label, "search", rows, clock())
    return shortlist.shortlist(slug, gateway=gateway, now=clock())


def ids(doc: dict) -> list[str]:
    return [c["id"] for c in doc["candidates"]]


def test_soft_avoid_sorts_never_filters(base: str) -> None:
    prefs.set_patch({"avoid_airlines": [{"code": "NH", "name": "ANA", "strength": "soft"}]})
    rows = [
        api_row("SOFT", "SFO", "NRT", "2026-09-05", "united", biz("80000", airlines="NH")),
        api_row("CLEAN", "SFO", "HND", "2026-09-06", "united", biz("90000", airlines="UA")),
    ]
    doc = run(base, rows)
    # The soft-avoided cheaper row is kept, just ranked below the clean pricier one.
    assert ids(doc) == ["CLEAN", "SOFT"]
    by_id = {c["id"]: c for c in doc["candidates"]}
    assert by_id["SOFT"]["soft"] is True
    assert by_id["CLEAN"]["soft"] is False


def test_gateway_mode_omits_dest_veto(base: str) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    set_plan(
        base,
        hybrid={"gateways": ["NRT", "ICN"], "onward_dests": ["OKA"], "max_hybrids": 3},
    )
    direct = run(
        base,
        [
            api_row("D-NRT", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
            api_row("D-ICN", "SFO", "ICN", "2026-09-06", "united", biz("70000")),
        ],
        label="asia",
    )
    assert set(ids(direct)) == {"D-NRT"}  # ICN vetoed as an endpoint
    gateways = run(
        base,
        [
            api_row("G-NRT", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
            api_row("G-ICN", "SFO", "ICN", "2026-09-06", "united", biz("70000")),
        ],
        gateway=True,
        label="gateways",
    )
    assert set(ids(gateways)) == {"G-NRT", "G-ICN"}  # gateways are waypoints, veto omitted


def test_expansion_buffer_precedes_classification(base: str) -> None:
    set_plan(base, max_finalists=2)  # buffer = min(2*2, 12) = 4
    rows = [
        api_row(f"R{i}", "SFO", dest, f"2026-09-1{i}", "united", biz(str(70000 + i * 1000)))
        for i, dest in enumerate(DESTS[:6])
    ]
    doc = run(base, rows)
    assert len(doc["candidates"]) == 4  # truncated to the buffer, not to max_finalists (2)
    assert doc["considered"] == 6


def test_hard_avoid_drops_only_when_every_airline_avoided(base: str) -> None:
    prefs.set_patch({"avoid_airlines": [{"code": "AA", "name": "American", "strength": "hard"}]})
    rows = [
        api_row("ALL", "SFO", "NRT", "2026-09-05", "united", biz("80000", airlines="AA")),
        api_row("MIX", "SFO", "HND", "2026-09-06", "united", biz("81000", airlines="AA, UA")),
        api_row("NONE", "SFO", "BKK", "2026-09-07", "united", biz("82000", airlines="UA")),
    ]
    doc = run(base, rows)
    assert set(ids(doc)) == {"MIX", "NONE"}


@pytest.mark.parametrize(
    ("seats", "kept"),
    [
        pytest.param(0, True, id="absent-seats-pass"),
        pytest.param(1, False, id="one-seat-drops-party-two"),
        pytest.param(2, True, id="exactly-party-passes"),
        pytest.param(3, True, id="surplus-passes"),
    ],
)
def test_seats_ge_party_with_absent_passthrough(base: str, seats: int, kept: bool) -> None:
    trips.set_patch(base, {"party": 2})
    row = api_row("R", "SFO", "NRT", "2026-09-05", "united", biz("80000", seats=seats))
    doc = run(base, [row])
    assert (ids(doc) == ["R"]) is kept


def test_mileage_ceiling_filters(base: str) -> None:
    set_plan(base, mileage_ceiling=100000)
    rows = [
        api_row("UNDER", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
        api_row("OVER", "SFO", "HND", "2026-09-06", "united", biz("120000")),
    ]
    doc = run(base, rows)
    assert set(ids(doc)) == {"UNDER"}


def test_sources_filter(base: str) -> None:
    set_plan(base, sources=["aeroplan"])
    rows = [
        api_row("KEEP", "SFO", "NRT", "2026-09-05", "aeroplan", biz("80000", airlines="AC")),
        api_row("DROP", "SFO", "HND", "2026-09-06", "united", biz("70000")),
    ]
    doc = run(base, rows)
    assert set(ids(doc)) == {"KEEP"}


def test_group_best_keeps_lowest_mileage_per_key(base: str) -> None:
    rows = [
        api_row("CHEAP", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
        api_row("PRICEY", "SFO", "NRT", "2026-09-05", "united", biz("95000")),
    ]
    doc = run(base, rows)
    assert ids(doc) == ["CHEAP"]
    assert doc["candidates"][0]["mileage"] == 80000


def test_departure_day_match_breaks_mileage_tie(base: str) -> None:
    match_day = shortlist._weekday_token("2026-09-05")
    prefs.set_patch({"departure_days": [match_day]})
    rows = [
        api_row("OFFDAY", "SFO", "HND", "2026-09-06", "united", biz("80000")),
        api_row("ONDAY", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
    ]
    doc = run(base, rows)
    assert ids(doc) == ["ONDAY", "OFFDAY"]
    by_id = {c["id"]: c for c in doc["candidates"]}
    assert by_id["ONDAY"]["departure_day_match"] is True
    assert by_id["OFFDAY"]["departure_day_match"] is False
