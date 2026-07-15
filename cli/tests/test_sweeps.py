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


def ok(
    rows: list[dict],
    has_more: bool = False,
    remaining: str = "900",
    cursor: str = "cursor",
) -> httpx.Response:
    payload = {"data": rows, "hasMore": has_more}
    if has_more:
        payload["cursor"] = cursor
    return httpx.Response(200, json=payload, headers={"X-RateLimit-Remaining": remaining})


def _provenance(slug: str) -> dict:
    envelope = json.loads(trips.artifact_read(slug, "legs/outbound/sweep-asia.json"))
    return envelope["provenance"]


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
    assert params["order_by"] == "lowest_mileage"


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
def test_search_sweep_merges_multi_page_rows(search_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("R1", dest="NRT")], has_more=True, cursor="page-2"),
            ok([biz("R2", dest="BKK")]),
        ]
    )
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert result["calls"] == 2
    assert result["completeness"] == "complete"
    assert [row["ID"] for row in envelope["rows"]] == ["R1", "R2"]
    assert envelope["search_states"] == {
        "NRT": {"state": "complete"},
        "BKK": {"state": "complete"},
    }
    assert route.call_count == 2


@pytest.mark.parametrize(
    ("final_has_more", "expected_states", "expected_completeness"),
    [
        pytest.param(
            True,
            {
                "NRT": {"state": "partial", "reason": "page_budget", "has_more": True},
                "BKK": {"state": "partial", "reason": "page_budget", "has_more": True},
            },
            "partial",
            id="budget-exhausted-api-has-more",
        ),
        pytest.param(
            False,
            {"NRT": {"state": "complete"}, "BKK": {"state": "searched_empty"}},
            "complete",
            id="budget-exhausted-api-complete",
        ),
    ],
)
@respx.mock
def test_search_sweep_uses_trailing_has_more_at_page_budget(
    search_trip: str,
    final_has_more: bool,
    expected_states: dict,
    expected_completeness: str,
) -> None:
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("R1")], has_more=True, cursor="page-2"),
            ok([biz("R2")], has_more=True, cursor="page-3"),
            ok([biz("R3")], has_more=final_has_more, cursor="page-4"),
        ]
    )
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert result["calls"] == 3
    assert result["completeness"] == expected_completeness
    assert [row["ID"] for row in envelope["rows"]] == ["R1", "R2", "R3"]
    assert envelope["search_states"] == expected_states
    assert route.call_count == 3
    assert [call.request.url.params["order_by"] for call in route.calls] == [
        "lowest_mileage",
        "lowest_mileage",
        "lowest_mileage",
    ]


def test_sweep_node_quota_cost_covers_each_page_of_each_widen(search_trip: str) -> None:
    graph = trips.compile_graph(search_trip)
    node = next(node for node in graph["nodes"] if node["id"] == "sweep:outbound:asia")
    assert node["quota_cost"] == 9


@respx.mock
def test_failed_carries_retryability(search_trip: str) -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectError("boom"))
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert result["completeness"] == "failed"
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert envelope["search_states"]["NRT"]["state"] == "failed"
    assert envelope["search_states"]["NRT"]["retryability"] == "retryable"


@respx.mock
def test_error_only_run_reports_failed_with_no_coverage(search_trip: str) -> None:
    # Finding 2: a first-call 500 completes a call (counts for quota) but returns no page — zero
    # data coverage, so the run is 'failed' with an empty searched list, not a fabricated 'partial'.
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert result["completeness"] == "failed"
    assert result["calls"] == 1  # the completed error response still counts for quota
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert envelope["provenance"]["searched"] == []  # no window earned coverage
    assert envelope["search_states"]["NRT"]["state"] == "failed"
    assert envelope["search_states"]["NRT"]["retryability"] == "retryable"


@respx.mock
def test_mid_pagination_quota_floor_preserves_partial_progress(search_trip: str) -> None:
    connect(cache_db(), now=clock()).record_quota("/search", 901)
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")], has_more=True, remaining="900"))
    result = sweeps.run(search_trip, "outbound:asia", quota_floor=900, now=clock())
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert result["rows"] == 1
    assert result["calls"] == 1
    assert result["completeness"] == "partial"
    assert [row["ID"] for row in envelope["rows"]] == ["R1"]
    assert envelope["search_states"]["NRT"] == {
        "state": "partial",
        "reason": "quota_budget",
    }
    assert envelope["provenance"]["searched"] == [{"start": "2026-08-25", "end": "2026-09-21"}]
    assert route.call_count == 1


@respx.mock
def test_mid_pagination_http_error_preserves_partial_progress(search_trip: str) -> None:
    error = httpx.ConnectError("boom")
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("R1")], has_more=True),
            error,
        ]
    )
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert result["rows"] == 1
    assert result["calls"] == 1
    assert result["completeness"] == "partial"
    assert [row["ID"] for row in envelope["rows"]] == ["R1"]
    assert envelope["search_states"]["NRT"] == {
        "state": "partial",
        "reason": str(error),
        "retryability": "retryable",
    }
    assert route.call_count == 2


@respx.mock
def test_not_run_when_quota_floor_reached(search_trip: str) -> None:
    connect(cache_db(), now=clock()).record_quota("/search", 900)
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    result = sweeps.run(search_trip, "outbound:asia", quota_floor=900, now=clock())
    assert result["completeness"] == "not_run"
    assert result["calls"] == 0
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


@pytest.mark.parametrize(
    ("disappeared", "expected_ids"),
    [
        pytest.param(2, ["GONE-00", "GONE-01"], id="two-disappearances"),
        pytest.param(
            51,
            [f"GONE-{index:02d}" for index in range(50)],
            id="fifty-one-disappearances-capped-at-fifty-ids",
        ),
    ],
)
@respx.mock
def test_refresh_records_in_window_superseded_rows(
    search_trip: str, disappeared: int, expected_ids: list[str]
) -> None:
    gone = [biz(f"GONE-{index:02d}") for index in range(disappeared)]
    keep = biz("KEEP")
    respx.get(SEARCH_URL).mock(side_effect=[ok([*gone, keep]), ok([keep])])

    sweeps.run(search_trip, "outbound:asia", now=clock())
    sweeps.run(search_trip, "outbound:asia", refresh=True, now=clock())

    assert _provenance(search_trip)["superseded_rows"] == {
        "count": disappeared,
        "ids": expected_ids,
    }


@respx.mock
def test_identical_refresh_omits_superseded_rows(search_trip: str) -> None:
    row = biz("SAME")
    respx.get(SEARCH_URL).mock(side_effect=[ok([row]), ok([row])])

    sweeps.run(search_trip, "outbound:asia", now=clock())
    sweeps.run(search_trip, "outbound:asia", refresh=True, now=clock())

    assert "superseded_rows" not in _provenance(search_trip)


@respx.mock
def test_refresh_excludes_out_of_window_superseded_rows(search_trip: str) -> None:
    in_window = biz("GONE-IN", date="2026-09-05")
    out_of_window = biz("GONE-OUT", date="2026-09-22")
    keep = biz("KEEP")
    respx.get(SEARCH_URL).mock(side_effect=[ok([in_window, out_of_window, keep]), ok([keep])])

    sweeps.run(search_trip, "outbound:asia", now=clock())
    sweeps.run(search_trip, "outbound:asia", refresh=True, now=clock())

    assert _provenance(search_trip)["superseded_rows"] == {
        "count": 1,
        "ids": ["GONE-IN"],
    }


@respx.mock
def test_first_sweep_omits_superseded_rows(search_trip: str) -> None:
    respx.get(SEARCH_URL).mock(return_value=ok([biz("FIRST")]))

    sweeps.run(search_trip, "outbound:asia", now=clock())

    assert "superseded_rows" not in _provenance(search_trip)


@respx.mock
def test_widen_refusal_after_prior_call_reports_partial(search_trip: str) -> None:
    # quota floor+1: the base window runs empty (1 call), then the widen is refused
    # pre-request; ran-ness comes from the accumulated calls, so this is partial, not not_run.
    connect(cache_db(), now=clock()).record_quota("/search", 901)
    route = respx.get(SEARCH_URL).mock(return_value=ok([], remaining="900"))
    result = sweeps.run(search_trip, "outbound:asia", quota_floor=900, now=clock())
    assert result["calls"] == 1
    assert result["completeness"] == "partial"
    assert route.call_count == 1
    envelope = json.loads(trips.artifact_read(search_trip, "legs/outbound/sweep-asia.json"))
    assert envelope["search_states"]["NRT"] == {"state": "partial", "reason": "quota_budget"}
    assert envelope["provenance"]["searched"] == [{"start": "2026-08-25", "end": "2026-09-21"}]


@respx.mock
def test_complete_refresh_carries_forward_out_of_window_rows(search_trip: str) -> None:
    keep = biz("KEEP", date="2026-09-05")
    widened = biz("WIDE", date="2026-09-25")  # beyond the base window's 2026-09-21 end
    respx.get(SEARCH_URL).mock(side_effect=[ok([keep, widened]), ok([keep])])

    sweeps.run(search_trip, "outbound:asia", now=clock())
    sweeps.run(search_trip, "outbound:asia", refresh=True, now=clock())

    assert "superseded_rows" not in _provenance(search_trip)  # WIDE is out of scope, not disclosed
    visible = {
        row["id"]
        for row in connect(cache_db(), now=clock()).query_availability(
            trip_slug=search_trip, labels=["outbound:asia"]
        )
    }
    assert visible == {"KEEP", "WIDE"}  # WIDE carried into the refreshed generation


@respx.mock
def test_captures_inputs_fp_before_fetch(search_trip: str) -> None:
    def edit_then_respond(request: httpx.Request) -> httpx.Response:
        october = {"start": "2026-10-01", "end": "2026-10-30", "trip_length_days": 12}
        trips.set_patch(search_trip, {"window": october})
        return ok([biz("R1")])

    respx.get(SEARCH_URL).mock(side_effect=edit_then_respond)
    sweeps.run(search_trip, "outbound:asia", now=clock())
    assert trips.phase_check(search_trip, "sweep:outbound:asia", now=clock())[0] is False


# --- scope building: region expansion and direction fidelity ---


_SEARCHED = [{"start": "2026-09-01", "end": "2026-09-14"}]


def test_scope_search_leg_resolves_region_to_demonstrated_keeps_concrete() -> None:
    leg = {"endpoint_field": "dest", "origins": ["SFO"], "dests": ["ASA", "SGN"], "source": None}
    scope = sweeps._scope(leg, [biz("r", dest="NRT")], _SEARCHED, None)
    dests = {entry["constraints"]["dest"] for entry in scope}
    assert dests == {"NRT", "SGN"}  # ASA resolves to demonstrated NRT; concrete SGN stays as-is
    assert {entry["constraints"]["origin"] for entry in scope} == {"SFO"}
    assert "ASA" not in dests  # the region token never reaches a concrete constraint
    assert all("source" not in e["constraints"] for e in scope)  # unrestricted: no source pin
    assert all((e["start"], e["end"]) == ("2026-09-01", "2026-09-14") for e in scope)


def test_scope_search_leg_region_with_no_rows_contributes_nothing() -> None:
    leg = {"endpoint_field": "dest", "origins": ["SFO"], "dests": ["ASA"], "source": None}
    scope = sweeps._scope(leg, [], _SEARCHED, None)
    assert scope == []  # a region proved by no rows carries its prior rows forward, undisclosed


def test_scope_search_leg_source_restricted_pins_each_source() -> None:
    leg = {"endpoint_field": "dest", "origins": ["SFO"], "dests": ["NRT"], "source": None}
    scope = sweeps._scope(leg, [biz("r", dest="NRT")], _SEARCHED, ["united", "aeroplan"])
    assert {entry["constraints"]["source"] for entry in scope} == {"united", "aeroplan"}
    routes = {(e["constraints"]["origin"], e["constraints"]["dest"]) for e in scope}
    assert routes == {("SFO", "NRT")}


@pytest.mark.parametrize(
    ("leg", "expected"),
    [
        pytest.param(
            {"endpoint_field": None, "source": "ap", "dest_region": "Asia", "origin_region": None},
            {"source": "ap", "dest_region": "Asia"},
            id="dest-region",
        ),
        pytest.param(
            {
                "endpoint_field": None,
                "source": "ap",
                "dest_region": None,
                "origin_region": "Africa",
            },
            {"source": "ap", "origin_region": "Africa"},
            id="origin-region",
        ),
        pytest.param(
            {
                "endpoint_field": None,
                "source": "ap",
                "dest_region": "Asia",
                "origin_region": "Africa",
            },
            {"source": "ap", "origin_region": "Africa", "dest_region": "Asia"},
            id="both-regions-one-conjunctive-group",
        ),
    ],
)
def test_scope_availability_leg_carries_source_and_region_direction(
    leg: dict, expected: dict
) -> None:
    scope = sweeps._scope(leg, [], _SEARCHED, None)
    assert scope == [{"start": "2026-09-01", "end": "2026-09-14", "constraints": expected}]


def _bucket_trip(monkeypatch: pytest.MonkeyPatch, dests: list[str]) -> str:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "trip_type": "one_way",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": dests}],
            }
        ),
    )
    return SLUG


@respx.mock
def test_region_dest_sweep_supersedes_a_demonstrated_airport(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _bucket_trip(monkeypatch, ["ASA"])
    respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("GONE", dest="NRT"), biz("KEEP", dest="HKG")]),
            ok([biz("STILL", dest="NRT"), biz("KEEP", dest="HKG")]),
        ]
    )
    sweeps.run(slug, "outbound:asia", now=clock())
    sweeps.run(slug, "outbound:asia", refresh=True, now=clock())
    # NRT is demonstrated again on refresh, so GONE's disappearance from it is disclosed
    assert _provenance(slug)["superseded_rows"]["ids"] == ["GONE"]


@respx.mock
def test_region_dest_sweep_spares_an_undemonstrated_member(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The reviewer repro: ASA's registry superset would wrongly supersede NRT the refresh never
    # proved. Demonstration scopes only HKG, so GONE@NRT carries forward, undisclosed.
    slug = _bucket_trip(monkeypatch, ["ASA"])
    respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("GONE", dest="NRT"), biz("KEEP", dest="HKG")]),
            ok([biz("KEEP", dest="HKG")]),  # NRT not demonstrated this run
        ]
    )
    sweeps.run(slug, "outbound:asia", now=clock())
    sweeps.run(slug, "outbound:asia", refresh=True, now=clock())
    assert "superseded_rows" not in _provenance(slug)
    visible = {
        row["id"]
        for row in connect(cache_db(), now=clock()).query_availability(
            trip_slug=slug, labels=["outbound:asia"]
        )
    }
    assert visible == {"KEEP", "GONE"}  # GONE stays visible, its NRT expansion unprovable


@respx.mock
def test_null_airport_region_supersedes_via_demonstration(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # QAF packages no registry airport list; demonstration supersedes it with no special case.
    slug = _bucket_trip(monkeypatch, ["QAF"])
    respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("GONE", dest="CMN"), biz("KEEP", dest="CAI")]),
            ok([biz("STILL", dest="CMN"), biz("KEEP", dest="CAI")]),
        ]
    )
    sweeps.run(slug, "outbound:asia", now=clock())
    sweeps.run(slug, "outbound:asia", refresh=True, now=clock())
    assert _provenance(slug)["superseded_rows"]["ids"] == ["GONE"]  # CMN demonstrated again


def _sourced_trip(monkeypatch: pytest.MonkeyPatch, sources: list[str]) -> str:
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
                "sources": sources,
            }
        ),
    )
    return SLUG


@respx.mock
def test_plan_sources_edit_complete_refresh_spares_removed_source(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Finding 1(a): dropping a source from plan.sources then a complete refresh must not fabricate
    # the disappearance of the removed source's rows — the search leg scopes on its plan sources.
    slug = _sourced_trip(monkeypatch, ["united", "aeroplan"])
    united = api_row("KEEP", "SFO", "NRT", "2026-09-05", "united", {"J": {"mileage": "80000"}})
    aeroplan = api_row("GONE", "SFO", "NRT", "2026-09-05", "aeroplan", {"J": {"mileage": "80000"}})
    respx.get(SEARCH_URL).mock(side_effect=[ok([united, aeroplan]), ok([united])])
    sweeps.run(slug, "outbound:asia", now=clock())
    trips.set_patch(
        slug,
        make_trip(
            {
                "trip_type": "one_way",
                "origins": ["SFO"],
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "sources": ["united"],  # drop aeroplan
            }
        ),
    )
    sweeps.run(slug, "outbound:asia", refresh=True, now=clock())
    assert "superseded_rows" not in _provenance(slug)  # the removed source's row is not disclosed
    visible = {
        row["id"]
        for row in connect(cache_db(), now=clock()).query_availability(
            trip_slug=slug, labels=["outbound:asia"]
        )
    }
    assert visible == {"KEEP", "GONE"}  # GONE carries forward, outside the united-only scope


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
