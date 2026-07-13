import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from _api import api_row

from getaway import prefs, sweeps, trips

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
SEARCH_URL = "https://seats.aero/partnerapi/search"


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def make_trip(plan: dict, cabin: str = "business") -> dict:
    return {
        "window": {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10},
        "cabin": cabin,
        "plan": plan,
    }


def one_row() -> dict:
    cabins = {"J": {"mileage": "80000", "seats": 2}}
    return api_row("R1", "SFO", "NRT", "2026-09-05", "united", cabins)


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        pytest.param(
            {"origins": ["SFO", "LAX"], "buckets": [{"name": "asia", "dests": ["NRT", "BKK"]}]},
            [("asia", "search")],
            id="single-bucket",
        ),
        pytest.param(
            {
                "origins": ["SFO"],
                "buckets": [
                    {"name": "asia", "dests": ["NRT"]},
                    {"name": "europe", "dests": ["LHR", "CDG"]},
                ],
            },
            [("asia", "search"), ("europe", "search")],
            id="two-buckets",
        ),
        pytest.param(
            {"origins": ["SFO"], "program_sweeps": [{"source": "ap", "dest_region": "Africa"}]},
            [("ap-africa", "availability")],
            id="program-sweep-dest-region",
        ),
        pytest.param(
            {"origins": ["SFO"], "program_sweeps": [{"source": "ap", "origin_region": "Asia"}]},
            [("ap-asia", "availability")],
            id="program-sweep-origin-region",
        ),
        pytest.param(
            {
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "hybrid": {"gateways": ["NRT", "ICN"], "onward_dests": ["OKA"], "max_hybrids": 3},
            },
            [("asia", "search"), ("gateways", "search")],
            id="bucket-plus-hybrid",
        ),
        pytest.param(
            {
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "program_sweeps": [{"source": "aeroplan", "dest_region": "Africa"}],
                "hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA"], "max_hybrids": 2},
            },
            [("asia", "search"), ("aeroplan-africa", "availability"), ("gateways", "search")],
            id="full-matrix",
        ),
        pytest.param({}, [], id="empty-plan"),
    ],
)
def test_derive_specs_matrix(plan: dict, expected: list[tuple[str, str]]) -> None:
    specs = sweeps.derive_specs(make_trip(plan), {})
    assert [(s["label"], s["kind"]) for s in specs] == expected


def test_derive_specs_search_params_include_cabin_window_sources() -> None:
    plan = {
        "origins": ["SFO", "LAX"],
        "buckets": [{"name": "asia", "dests": ["NRT", "BKK"]}],
        "sources": ["united", "aeroplan"],
    }
    (spec,) = sweeps.derive_specs(make_trip(plan), {})
    assert spec["params"] == {
        "origins": ["SFO", "LAX"],
        "dests": ["NRT", "BKK"],
        "start": "2026-09-01",
        "end": "2026-09-14",
        "cabins": ["business"],
        "sources": ["united", "aeroplan"],
    }


def test_derive_specs_availability_params() -> None:
    plan = {"origins": ["SFO"], "program_sweeps": [{"source": "aeroplan", "dest_region": "Africa"}]}
    (spec,) = sweeps.derive_specs(make_trip(plan), {})
    assert spec["params"] == {
        "source": "aeroplan",
        "cabin": "business",
        "start": "2026-09-01",
        "end": "2026-09-14",
        "dest_region": "Africa",
    }


@pytest.fixture
def hybrid_trip(getaway_home: Path) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "window": {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10},
            "plan": {
                "origins": ["SFO"],
                "hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA", "KIX"], "max_hybrids": 3},
            },
        },
    )
    return SLUG


def test_onward_spec_derives_from_gateway_artifact(hybrid_trip: str) -> None:
    trips.artifact_write(
        hybrid_trip,
        "shortlist-gateway.json",
        json.dumps({"candidates": [{"dest": "NRT"}, {"dest": "NRT"}], "considered": 2}),
    )
    trip = trips.show(hybrid_trip)
    spec = sweeps._onward_spec(hybrid_trip, trip)
    assert spec == {
        "label": "onward",
        "kind": "search",
        "params": {
            "origins": ["NRT"],
            "dests": ["OKA", "KIX"],
            "start": "2026-09-01",
            "end": "2026-09-14",
        },
    }


@pytest.fixture
def search_trip(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "window": {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10},
            "plan": {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        },
    )
    return SLUG


@respx.mock
def test_run_calls_ingests_writes_artifact_and_stamps(search_trip: str) -> None:
    row = one_row()
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [row], "hasMore": False}, headers={"X-RateLimit-Remaining": "900"}
        )
    )
    result = sweeps.run(search_trip, "asia", now=clock())
    assert result == {"label": "asia", "rows": 1, "new": 1, "quota_remaining": 900}
    assert route.call_count == 1
    artifact = trips.artifact_read(search_trip, "sweep-asia.jsonl")
    assert [json.loads(line)["ID"] for line in artifact.splitlines()] == ["R1"]
    checkpoints = json.loads((trips.trip_dir(search_trip) / "checkpoints.json").read_text())
    assert checkpoints["sweep:asia"]["quota_after"] == 900


@respx.mock
def test_run_fresh_skip_spends_zero_http(search_trip: str) -> None:
    row = one_row()
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [row], "hasMore": False}, headers={"X-RateLimit-Remaining": "900"}
        )
    )
    sweeps.run(search_trip, "asia", now=clock())
    assert route.call_count == 1
    again = sweeps.run(search_trip, "asia", now=clock())
    assert again == {"label": "asia", "skipped": True, "rows": 1}
    assert route.call_count == 1


@respx.mock
def test_run_refresh_forces_http(search_trip: str) -> None:
    row = one_row()
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [row], "hasMore": False}, headers={"X-RateLimit-Remaining": "900"}
        )
    )
    sweeps.run(search_trip, "asia", now=clock())
    sweeps.run(search_trip, "asia", refresh=True, now=clock())
    assert route.call_count == 2
