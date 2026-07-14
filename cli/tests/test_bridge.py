import datetime as dt
import json
import types
from collections.abc import Callable
from pathlib import Path

import pytest

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
        "source": "fli",
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


def test_fli_success_never_calls_serpapi(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_trip()
    write_onward([PAIR])
    serp_calls: list[str] = []

    monkeypatch.setattr(bridge, "_search_flights", lambda g, d, date: [fake_result(120.0)])

    def resolve_api_key_if_available() -> str:
        serp_calls.append("resolve")
        return "serp-key"

    def serp_search(
        origin: str, dest: str, date: str, cabin: str, api_key: str | None = None
    ) -> list:
        serp_calls.append("search")
        return [fake_result(90.0)]

    monkeypatch.setattr(
        bridge.serp, "resolve_api_key_if_available", resolve_api_key_if_available
    )
    monkeypatch.setattr(bridge.serp, "search", serp_search)

    assert bridge.run(SLUG, now=clock()) == {"quotes": 1, "failures": 0}
    assert serp_calls == []
    assert bridge_out()["quotes"][0]["source"] == "fli"


def test_fli_error_falls_back_to_serpapi(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_trip()
    write_onward([PAIR])
    calls: list[tuple] = []

    def fli_search(origin: str, dest: str, date: str) -> list:
        raise RuntimeError("google 500")

    def serp_search(
        origin: str, dest: str, date: str, cabin: str, api_key: str | None = None
    ) -> list:
        calls.append((origin, dest, date, cabin, api_key))
        return [fake_result(90.0, airline="JL", fn="JL901")]

    monkeypatch.setattr(bridge, "_search_flights", fli_search)
    monkeypatch.setattr(bridge.serp, "resolve_api_key_if_available", lambda: "serp-key")
    monkeypatch.setattr(bridge.serp, "search", serp_search)

    assert bridge.run(SLUG, now=clock()) == {"quotes": 1, "failures": 0}
    assert calls == [("NRT", "OKA", "2026-09-10", "economy", "serp-key")]
    assert bridge_out()["quotes"][0] == {
        "gateway": "NRT",
        "onward_dest": "OKA",
        "cabin": "economy",
        "source": "serpapi",
        "price": 90.0,
        "currency": "USD",
        "duration_minutes": 180,
        "stops": 0,
        "airline": "JL",
        "flight_number": "JL901",
        "departs_local": "2026-09-10T09:00",
        "arrives_local": "2026-09-10T12:00",
    }


@pytest.mark.parametrize(
    "fli_results",
    [None, [], [fake_result(None)]],
    ids=["none", "empty", "unpriced"],
)
def test_fli_without_priced_results_falls_back_to_serpapi(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch, fli_results: list | None
) -> None:
    make_trip()
    write_onward([PAIR])
    calls: list[tuple] = []

    monkeypatch.setattr(bridge, "_search_flights", lambda g, d, date: fli_results)
    monkeypatch.setattr(bridge.serp, "resolve_api_key_if_available", lambda: "serp-key")

    def serp_search(
        origin: str, dest: str, date: str, cabin: str, api_key: str | None = None
    ) -> list:
        calls.append((origin, dest, date, cabin, api_key))
        return [fake_result(80.0)]

    monkeypatch.setattr(bridge.serp, "search", serp_search)

    assert bridge.run(SLUG, now=clock()) == {"quotes": 1, "failures": 0}
    assert calls == [("NRT", "OKA", "2026-09-10", "economy", "serp-key")]
    assert bridge_out()["quotes"][0]["source"] == "serpapi"


def test_both_search_backends_fail_once(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_trip()
    write_onward([PAIR])

    def fli_search(origin: str, dest: str, date: str) -> list:
        raise RuntimeError("google 500")

    def serp_search(
        origin: str, dest: str, date: str, cabin: str, api_key: str | None = None
    ) -> list:
        raise RuntimeError("serpapi 500")

    monkeypatch.setattr(bridge, "_search_flights", fli_search)
    monkeypatch.setattr(bridge.serp, "resolve_api_key_if_available", lambda: "serp-key")
    monkeypatch.setattr(bridge.serp, "search", serp_search)

    assert bridge.run(SLUG, now=clock()) == {"quotes": 0, "failures": 1}
    assert bridge_out() == {
        "quotes": [],
        "failures": [
            {
                "gateway": "NRT",
                "onward_dest": "OKA",
                "date": "2026-09-10",
                "reason": "search error: serpapi 500",
                "retryable": True,
            }
        ],
    }


def test_missing_serpapi_key_preserves_fli_failure_detail(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_trip()
    write_onward([PAIR])
    serp_calls: list[str] = []

    def fli_search(origin: str, dest: str, date: str) -> list:
        raise RuntimeError("google 500")

    def serp_search(
        origin: str, dest: str, date: str, cabin: str, api_key: str | None = None
    ) -> list:
        serp_calls.append("search")
        return [fake_result(90.0)]

    monkeypatch.setattr(bridge, "_search_flights", fli_search)
    monkeypatch.setattr(bridge.serp, "resolve_api_key_if_available", lambda: None)
    monkeypatch.setattr(bridge.serp, "search", serp_search)

    assert bridge.run(SLUG, now=clock()) == {"quotes": 0, "failures": 1}
    assert serp_calls == []
    assert bridge_out() == {
        "quotes": [],
        "failures": [
            {
                "gateway": "NRT",
                "onward_dest": "OKA",
                "date": "2026-09-10",
                "reason": "search error: google 500; fallback: no serpapi key",
                "retryable": True,
            }
        ],
    }


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
