import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from _api import api_row, shortlist_doc
from click.testing import CliRunner

from getaway import prefs, sweeps, trips
from getaway.paths import cache_db
from getaway.store import NoData, connect

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
SEARCH_URL = "https://seats.aero/partnerapi/search"
WINDOW = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def make_trip(plan: dict, cabin: str = "business") -> dict:
    return {"window": WINDOW, "cabin": cabin, "plan": plan}


def biz(rid: str, dest: str = "NRT", origin: str = "SFO", date: str = "2026-09-05") -> dict:
    return api_row(rid, origin, dest, date, "united", {"J": {"mileage": "80000", "seats": 2}})


def ok(rows: list[dict], has_more: bool = False, remaining: str = "900") -> httpx.Response:
    return httpx.Response(
        200, json={"data": rows, "hasMore": has_more}, headers={"X-RateLimit-Remaining": remaining}
    )


@pytest.fixture
def search_trip(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "trip_type": "one_way",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT", "BKK"]}],
            }
        ),
    )
    return SLUG


# --- derive_specs ---


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        pytest.param(
            {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
            [("asia", "search")],
            id="single-bucket",
        ),
        pytest.param(
            {"origins": ["SFO"], "program_sweeps": [{"source": "ap", "dest_region": "Africa"}]},
            [("ap-africa", "availability")],
            id="program-sweep",
        ),
        pytest.param(
            {
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA"], "max_hybrids": 3},
            },
            [("asia", "search"), ("gateways", "search")],
            id="bucket-plus-hybrid",
        ),
        pytest.param({}, [], id="empty-plan"),
    ],
)
def test_derive_specs_matrix(plan: dict, expected: list[tuple[str, str]]) -> None:
    specs = sweeps.derive_specs(make_trip(plan), {})
    assert [(s["label"], s["kind"]) for s in specs] == expected


# --- retrieval policy ---


@respx.mock
def test_soft_window_pads_all_cabins_include_filtered(search_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-08-25"  # window.start - 7 padding
    assert params["end_date"] == "2026-09-21"  # window.end + 7 padding
    assert params["include_filtered"] == "true"
    assert "cabins" not in params  # all cabins ride one call
    assert params["take"] == "1000"


@respx.mock
def test_confirmed_constraint_sweeps_exact_window(search_trip: str) -> None:
    trips.set_patch(
        search_trip,
        {
            "plan": {
                "trip_type": "one_way",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "constraints": {
                    "outbound_departure_window": {
                        "start": "2026-09-03",
                        "end": "2026-09-09",
                        "confirmed": True,
                    }
                },
            }
        },
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-09-03"
    assert params["end_date"] == "2026-09-09"


@respx.mock
def test_run_writes_envelope_with_provenance_and_states(search_trip: str) -> None:
    respx.get(SEARCH_URL).mock(return_value=ok([biz("R1", dest="NRT")]))
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert result["rows"] == 1
    assert result["completeness"] == "complete"  # NRT found; per-endpoint BKK stays searched_empty
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert envelope["provenance"]["expanded_origins"] == ["SFO"]
    assert envelope["provenance"]["source"] == "all"
    assert envelope["search_states"]["NRT"] == {"state": "complete"}
    assert envelope["search_states"]["BKK"] == {"state": "searched_empty"}
    assert [r["ID"] for r in envelope["rows"]] == ["R1"]
    checkpoints = json.loads((trips.trip_dir(search_trip) / "checkpoints.json").read_text())
    assert checkpoints["sweep:outbound:asia"]["quota_after"] == 900


@respx.mock
def test_partial_when_page_budget_hit(search_trip: str) -> None:
    respx.get(SEARCH_URL).mock(return_value=ok([biz("R1", dest="NRT")], has_more=True))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    # an endpoint absent from a truncated page is unknown, not searched_empty
    assert envelope["search_states"]["BKK"] == {
        "state": "partial",
        "reason": "page_budget",
        "has_more": True,
    }
    assert envelope["search_states"]["NRT"]["state"] == "partial"


@respx.mock
def test_failed_carries_retryability(search_trip: str) -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectError("boom"))
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert result["completeness"] == "failed"
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert envelope["search_states"]["NRT"]["state"] == "failed"
    assert envelope["search_states"]["NRT"]["retryability"] == "retryable"


@respx.mock
def test_not_run_when_quota_floor_reached(search_trip: str) -> None:
    connect(cache_db(), now=clock()).record_quota("/search", 900)
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    result = sweeps.run(search_trip, "outbound:asia", quota_floor=900, now=clock())
    assert result["completeness"] == "not_run"
    assert route.call_count == 0  # reservation refused before the HTTP call
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert envelope["search_states"]["NRT"] == {"state": "not_run", "reason": "quota_budget"}


@respx.mock
def test_widens_dates_when_empty_under_call_budget(search_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert route.call_count == 3  # base + AUTO_WIDEN_CALL_BUDGET_PER_LEG (2)
    assert result["calls"] == 3
    windows = [
        (c.request.url.params["start_date"], c.request.url.params["end_date"]) for c in route.calls
    ]
    assert windows[0] == ("2026-08-25", "2026-09-21")
    assert windows[1] == ("2026-08-18", "2026-09-28")  # widened by DATE_WIDEN_STEP_DAYS each side


@respx.mock
def test_found_inventory_does_not_widen(search_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    assert route.call_count == 1


@respx.mock
def test_self_skip_spends_zero_http(search_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    assert route.call_count == 1
    again = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert again == {"key": "outbound:asia", "skipped": True, "rows": 1}
    assert route.call_count == 1


@respx.mock
def test_refresh_forces_http(search_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    sweeps.run(search_trip, "outbound:asia", refresh=True, now=clock())
    assert route.call_count == 2


@respx.mock
def test_captures_inputs_fp_before_fetch(search_trip: str) -> None:
    def edit_then_respond(request: httpx.Request) -> httpx.Response:
        october = {"start": "2026-10-01", "end": "2026-10-30", "trip_length_days": 12}
        trips.set_patch(search_trip, {"window": october})
        return ok([biz("R1")])

    respx.get(SEARCH_URL).mock(side_effect=edit_then_respond)
    sweeps.run(search_trip, "outbound:asia", now=clock())
    assert trips.phase_check(search_trip, "sweep:outbound:asia", now=clock())[0] is False


# --- return leg: lazy endpoint resolution ---


@pytest.fixture
def round_trip(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "trip_type": "round_trip",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT", "OKA"]}],
            }
        ),
    )
    trips.artifact_write(
        SLUG,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([{"dest": "NRT"}, {"dest": "NRT"}], considered=2)),
    )
    return SLUG


@respx.mock
def test_return_resolves_origins_from_outbound_shortlist(round_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(round_trip, "return", now=clock())
    params = route.calls[0].request.url.params
    assert params["origin_airport"] == "NRT"  # reached outbound destination
    assert params["destination_airport"] == "SFO"  # home


@pytest.fixture
def hybrid_trip(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "trip_type": "one_way",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA"], "max_hybrids": 3},
            }
        ),
    )
    trips.artifact_write(
        SLUG, "legs/outbound/shortlist-gateway.json", json.dumps(shortlist_doc(considered=0))
    )
    return SLUG


@respx.mock
def test_onward_empty_gateway_raises_nodata_without_http(hybrid_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(NoData, match="shortlist-gateway"):
        sweeps.run(hybrid_trip, "outbound:onward", now=clock())
    assert route.call_count == 0
