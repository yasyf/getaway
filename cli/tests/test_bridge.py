import datetime as dt
import json
import types
from collections.abc import Callable
from pathlib import Path

from getaway import bridge, prefs, trips

FROZEN = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.timezone.utc)  # 21:00 in JST
SLUG = "2026-09-warm"


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def fake_result(
    price: float | None,
    *,
    currency: str = "USD",
    duration: int = 180,
    stops: int = 0,
    airline: str = "NH",
    fn: str = "NH1",
    dep: dt.datetime = dt.datetime(2026, 9, 10, 9, 0),
    arr: dt.datetime = dt.datetime(2026, 9, 10, 12, 0),
) -> object:
    leg = types.SimpleNamespace(
        airline=types.SimpleNamespace(name=airline),
        flight_number=fn,
        departure_datetime=dep,
        arrival_datetime=arr,
    )
    return types.SimpleNamespace(
        price=price, currency=currency, duration=duration, stops=stops, legs=[leg]
    )


def make_trip() -> None:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "party": 1,
            "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10},
            "plan": {
                "trip_type": "round_trip",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA"], "max_hybrids": 3},
            },
        },
    )


def write_onward(pairs: list[dict]) -> None:
    trips.artifact_write(
        SLUG, "legs/outbound/onward.json", json.dumps({"minima": [], "bridge_pairs": pairs})
    )


def bridge_out() -> dict:
    return json.loads(trips.artifact_read(SLUG, "legs/outbound/bridge.json"))


PAIR = {"gateway": "NRT", "onward_dest": "OKA", "date": "2026-09-10"}


def test_quotes_the_cheapest_priced_result(getaway_home: Path) -> None:
    make_trip()
    write_onward([PAIR])

    def search(g: str, d: str, date: str) -> list:  # fli returns cheapest-first
        return [fake_result(120.0, airline="NH", fn="NH303"), fake_result(200.0)]

    assert bridge.run(SLUG, now=clock(), search=search) == {"quotes": 1, "failures": 0}
    quote = bridge_out()["quotes"][0]
    assert quote == {
        "gateway": "NRT",
        "onward_dest": "OKA",
        "cabin": "economy",
        "price": 120.0,
        "currency": "USD",
        "duration_minutes": 180,
        "stops": 0,
        "airline": "NH",
        "flight_number": "NH303",
        "departs_local": "2026-09-10T09:00",  # real observed Google Flights clock
        "arrives_local": "2026-09-10T12:00",
    }


def test_zero_results_is_failed_never_no_fare(getaway_home: Path) -> None:
    make_trip()
    write_onward([PAIR])
    assert bridge.run(SLUG, now=clock(), search=lambda g, d, date: None) == {
        "quotes": 0,
        "failures": 1,
    }
    doc = bridge_out()
    assert doc["quotes"] == []  # a viable route with zero results is never a "no cash fare" quote
    failure = doc["failures"][0]
    assert failure["reason"] == "no results returned"
    assert failure["retryable"] is True


def test_only_unpriced_results_is_failed(getaway_home: Path) -> None:
    make_trip()
    write_onward([PAIR])
    result = bridge.run(SLUG, now=clock(), search=lambda g, d, date: [fake_result(None)])
    assert result == {"quotes": 0, "failures": 1}
    assert bridge_out()["failures"][0]["retryable"] is True


def test_search_exception_is_retryable_failure(getaway_home: Path) -> None:
    make_trip()
    write_onward([PAIR])

    def boom(g: str, d: str, date: str) -> list:
        raise RuntimeError("google 500")

    assert bridge.run(SLUG, now=clock(), search=boom) == {"quotes": 0, "failures": 1}
    failure = bridge_out()["failures"][0]
    assert failure["retryable"] is True
    assert "search error" in failure["reason"]


def test_past_origin_local_date_is_nonretryable_and_never_queried(getaway_home: Path) -> None:
    make_trip()
    write_onward([{"gateway": "NRT", "onward_dest": "OKA", "date": "2026-08-01"}])  # past in JST
    calls: list[str] = []

    def search(g: str, d: str, date: str) -> list:
        calls.append(date)
        return [fake_result(120.0)]

    assert bridge.run(SLUG, now=clock(), search=search) == {"quotes": 0, "failures": 1}
    assert calls == []  # a date already past in the gateway's local day is never sent to Google
    failure = bridge_out()["failures"][0]
    assert failure["retryable"] is False
    assert "past in origin-local time" in failure["reason"]


def test_unknown_gateway_offset_skips_the_past_date_guard(getaway_home: Path) -> None:
    make_trip()
    write_onward([{"gateway": "ZZZ", "onward_dest": "OKA", "date": "2026-08-01"}])
    calls: list[str] = []

    def search(g: str, d: str, date: str) -> list:
        calls.append(date)
        return [fake_result(90.0)]

    bridge.run(SLUG, now=clock(), search=search)
    assert calls == [
        "2026-08-01"
    ]  # no offset known -> rely on the zero-results surface, still query


def test_oka_alias_fix_patches_encode_and_decode(getaway_home: Path) -> None:
    import urllib.parse

    bridge._install_oka_fix()
    from fli.models import Airport, FlightSearchFilters, FlightSegment, PassengerInfo, TripType
    from fli.search import _decoders

    assert _decoders._AIRPORT_BY_CODE["OKA"] is Airport["NAH"]  # decode cache learns OKA
    segment = FlightSegment(
        departure_airport=[[Airport["HND"], 0]],
        arrival_airport=[[Airport["OKA"], 0]],
        travel_date="2026-09-10",
    )
    filters = FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[segment],
    )
    wire = urllib.parse.unquote(filters.encode())
    assert '\\"OKA\\"' in wire  # the aliased NAH code is rewritten back to OKA on the wire
    assert '\\"NAH\\"' not in wire


def test_bridge_stamps_its_node(getaway_home: Path) -> None:
    make_trip()
    write_onward([PAIR])
    bridge.run(SLUG, now=clock(), search=lambda g, d, date: [fake_result(120.0)])
    assert trips.phase_check(SLUG, "bridge", now=clock())[1] is not None
