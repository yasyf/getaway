import datetime as dt
import json
import types
from pathlib import Path

import httpx
import pytest
import respx

from getaway import keys, serp

FIXTURES = Path(__file__).parent / "fixtures"
API_KEY = "serp-secret-test-key"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    ("cabin", "travel_class"),
    [("economy", 1), ("premium", 2), ("business", 3), ("first", 4)],
)
@respx.mock
def test_search_sends_exact_google_flights_params(cabin: str, travel_class: int) -> None:
    route = respx.get(serp.BASE_URL).mock(
        return_value=httpx.Response(200, json={"best_flights": [], "other_flights": []})
    )

    assert serp.search("NRT", "OKA", "2026-09-10", cabin, api_key=API_KEY) == []
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.method == "GET"
    assert dict(request.url.params) == {
        "engine": "google_flights",
        "departure_id": "NRT",
        "arrival_id": "OKA",
        "outbound_date": "2026-09-10",
        "type": "2",
        "currency": "USD",
        "travel_class": str(travel_class),
        "api_key": API_KEY,
    }


@respx.mock
def test_search_merges_sorts_and_normalizes_priced_options() -> None:
    respx.get(serp.BASE_URL).mock(
        return_value=httpx.Response(200, json=load("serpapi_flights.json"))
    )

    results = serp.search("NRT", "OKA", "2026-09-10", "economy", api_key=API_KEY)

    assert results == [
        types.SimpleNamespace(
            price=280,
            currency="USD",
            duration=300,
            stops=1,
            legs=[
                types.SimpleNamespace(
                    airline=types.SimpleNamespace(name="United Airlines"),
                    flight_number="UA 7951",
                    departure_datetime=dt.datetime(2026, 9, 10, 7, 15),
                    arrival_datetime=dt.datetime(2026, 9, 10, 9, 25),
                ),
                types.SimpleNamespace(
                    airline=types.SimpleNamespace(name="All Nippon Airways"),
                    flight_number="NH 1207",
                    departure_datetime=dt.datetime(2026, 9, 10, 10, 30),
                    arrival_datetime=dt.datetime(2026, 9, 10, 12, 15),
                ),
            ],
        ),
        types.SimpleNamespace(
            price=410,
            currency="USD",
            duration=175,
            stops=0,
            legs=[
                types.SimpleNamespace(
                    airline=types.SimpleNamespace(name="All Nippon Airways"),
                    flight_number="NH 463",
                    departure_datetime=dt.datetime(2026, 9, 10, 8, 0),
                    arrival_datetime=dt.datetime(2026, 9, 10, 10, 55),
                )
            ],
        ),
    ]


@respx.mock
def test_search_zero_results_returns_empty_without_leaking_key() -> None:
    respx.get(serp.BASE_URL).mock(
        return_value=httpx.Response(200, json={"best_flights": [], "other_flights": []})
    )

    result = serp.search("NRT", "OKA", "2026-09-10", "economy", api_key=API_KEY)

    assert result == []
    assert API_KEY not in str(result)
    assert API_KEY not in repr(result)


@respx.mock
def test_search_401_raises_auth_error_without_leaking_key() -> None:
    respx.get(serp.BASE_URL).mock(
        return_value=httpx.Response(401, json={"error": "Invalid API key"})
    )

    with pytest.raises(keys.AuthError) as caught:
        serp.search("NRT", "OKA", "2026-09-10", "economy", api_key=API_KEY)

    assert API_KEY not in str(caught.value)
    assert API_KEY not in repr(caught.value)


@respx.mock
def test_search_500_failure_does_not_leak_key() -> None:
    respx.get(serp.BASE_URL).mock(
        return_value=httpx.Response(500, json={"error": "upstream failure"})
    )

    with pytest.raises(
        serp.SerpApiError,
        match=r"^SerpApi request failed: HTTP 500 at https://serpapi\.com/search$",
    ) as caught:
        serp.search("NRT", "OKA", "2026-09-10", "economy", api_key=API_KEY)

    assert API_KEY not in str(caught.value)
    assert API_KEY not in repr(caught.value)


@respx.mock
def test_search_timeout_does_not_leak_key() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"request timed out at {request.url}", request=request)

    respx.get(serp.BASE_URL).mock(side_effect=timeout)

    with pytest.raises(
        serp.SerpApiError,
        match=r"^SerpApi request failed: ReadTimeout at https://serpapi\.com/search$",
    ) as caught:
        serp.search("NRT", "OKA", "2026-09-10", "economy", api_key=API_KEY)

    assert API_KEY not in str(caught.value)
    assert API_KEY not in repr(caught.value)
