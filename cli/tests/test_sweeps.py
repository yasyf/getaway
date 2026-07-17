import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from _api import api_row, shortlist_doc
from click.testing import CliRunner

from getaway import prefs, shortlist, sweeps, trips
from getaway.paths import UsageError, cache_db
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
            {"legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT", "BKK"]}]}]}
        ),
    )
    return SLUG


# --- derive_specs (per-leg) ---


@pytest.mark.parametrize(
    ("leg", "expected"),
    [
        pytest.param(
            {"buckets": [{"name": "asia", "dests": ["NRT"]}]},
            [("asia", "search")],
            id="single-bucket",
        ),
        pytest.param(
            {"program_sweeps": [{"source": "ap", "dest_region": "Africa"}]},
            [("ap-africa", "availability")],
            id="dest-region-program-sweep",
        ),
        pytest.param(
            {"program_sweeps": [{"source": "ap", "origin_region": "North America"}]},
            [("ap-from-north-america", "availability")],
            id="origin-region-program-sweep",
        ),
        pytest.param(
            {
                "buckets": [{"name": "asia", "dests": ["NRT"]}],
                "program_sweeps": [{"source": "ap", "dest_region": "Africa"}],
            },
            [("asia", "search"), ("ap-africa", "availability")],
            id="bucket-plus-program-sweep",
        ),
        pytest.param(
            {"dests": ["NRT"]},
            [(None, "search")],
            id="bare-leg-no-groupings",
        ),
    ],
)
def test_derive_specs_matrix(leg: dict, expected: list[tuple[str, str]]) -> None:
    specs = sweeps.derive_specs(leg)
    assert [(s["label"], s["kind"]) for s in specs] == expected


# --- optional-leg skip transparency (R-A) ---


def _sl_cand(dest: str, date: str = "2026-09-05") -> dict:
    return {"dest": dest, "date": date}


def test_chained_endpoints_folds_a_union_skip_source(search_trip: str) -> None:
    # a leading positioning leg's home origins ride in as a union-only skip source
    es = {"field": "dest", "union": ["LAX"], "override": None, "skip_sources": [{"union": ["SFO"]}]}
    origins, arrivals = sweeps._chained_endpoints(search_trip, "onward", es)
    assert origins == ["LAX", "SFO"]  # LAX (positioning present) + SFO (positioning skipped)
    assert arrivals == {}  # no `from` — union/skip carry no predecessor arrival dates


def test_chained_endpoints_resolves_a_from_skip_source(search_trip: str) -> None:
    trips.artifact_write(
        search_trip, "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([_sl_cand("NRT")], leg="outbound")),
    )
    trips.artifact_write(
        search_trip, "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([_sl_cand("BKK")], leg="hop")),
    )
    es = {
        "from": "legs/hop/shortlist.json", "field": "dest", "union": [], "override": None,
        "skip_sources": [{"from": "legs/outbound/shortlist.json", "field": "dest"}],
    }
    origins, _ = sweeps._chained_endpoints(search_trip, "return", es)
    assert origins == ["BKK", "NRT"]  # hop landings (present) + outbound landings (hop skipped)


def test_chained_endpoints_override_ignores_skip_sources(search_trip: str) -> None:
    # explicit origins REPLACE the chain — skip anchors never widen an open jaw
    es = {
        "field": "dest", "union": ["LAX"], "override": {"origins": ["LAX"]},
        "skip_sources": [{"union": ["SFO"]}],
    }
    origins, _ = sweeps._chained_endpoints(search_trip, "onward", es)
    assert origins == ["LAX"]


def test_leg_window_envelopes_full_and_skip_variant_windows() -> None:
    # R-A window union (MAJOR-2): the stay branch ENVELOPEs its own window and every skip variant's,
    # so a skip variant departing LATER (wider pre-boundary stay) is searched, not truncated.
    trip = {"window": {"start": "2026-09-01", "end": "2026-09-14"}, "plan": {"legs": []}}
    predecessor = {"stay_nights": {"min": 1, "max": 1}}  # optional hop: 09-05 + 1 night, tight
    arrivals = {"NRT": {"2026-09-05"}}
    leg = {"id": "hop2", "dests": ["SIN"]}
    tight = sweeps._leg_window(trip, leg, False, False, predecessor, arrivals)
    assert tight == ("2026-09-06", "2026-09-06")
    # skip variant off the pre-boundary (outbound 09-04 + stay {2,6} = 09-06..09-10) extends the end
    wide = sweeps._leg_window(
        trip, leg, False, False, predecessor, arrivals, [("2026-09-06", "2026-09-10")]
    )
    assert wide == ("2026-08-25", "2026-09-10")  # start floored to trip window - 7d, end enveloped


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
def test_soft_window_padding_honors_tuning_override(search_trip: str) -> None:
    trips.set_patch(
        search_trip,
        {
            "plan": {
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]}],
                "tuning": {"date_padding_days": 2},
            }
        },
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([biz("R1")]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-08-30"  # window.start - 2 (tuned padding, not default 7)
    assert params["end_date"] == "2026-09-16"  # window.end + 2


@respx.mock
def test_sweep_page_budget_honors_tuning_override(search_trip: str) -> None:
    trips.set_patch(
        search_trip,
        {
            "plan": {
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]}],
                "tuning": {"sweep_page_budget": 2},
            }
        },
    )
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            ok([biz("R1")], has_more=True, cursor="page-2"),
            ok([biz("R2")], has_more=True, cursor="page-3"),
            ok([biz("R3")], has_more=True, cursor="page-4"),
        ]
    )
    result = sweeps.run(search_trip, "outbound:asia", now=clock())
    assert result["calls"] == 2  # stops one page short of the default budget of 3
    assert route.call_count == 2
    assert result["completeness"] == "partial"  # trailing has_more at the tuned page budget


@respx.mock
def test_confirmed_constraint_sweeps_exact_window(search_trip: str) -> None:
    trips.set_patch(
        search_trip,
        {
            "plan": {
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]}],
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
        make_trip({"legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": dests}]}]}),
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
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]}],
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
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]}],
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
                "legs": [
                    {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT", "OKA"]}]},
                    {"id": "return", "dests": "$origins"},
                ]
            }
        ),
    )
    trips.artifact_write(
        SLUG,
        "legs/outbound/shortlist.json",
        json.dumps(
            shortlist_doc(
                [
                    {"dest": "NRT", "date": "2026-09-05"},
                    {"dest": "NRT", "date": "2026-09-06"},
                ],
                considered=2,
            )
        ),
    )
    return SLUG


@respx.mock
def test_return_resolves_origins_from_outbound_shortlist(round_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(round_trip, "return", now=clock())
    params = route.calls[0].request.url.params
    assert params["origin_airport"] == "NRT"  # reached outbound destination
    assert params["destination_airport"] == "SFO"  # home


def test_conventional_sweep_keys_and_paths_match_head(round_trip: str) -> None:
    # Byte-identity: a two-intent conventional plan compiles the same sweep node ids/commands and
    # artifact outputs the v2 round-trip graph did (sweep:outbound:asia, sweep:return).
    graph = trips.compile_graph(round_trip)
    sweeps_by_id = {n["id"]: n for n in graph["nodes"] if n["kind"] == "sweep"}
    assert set(sweeps_by_id) == {"sweep:outbound:asia", "sweep:return"}
    assert sweeps_by_id["sweep:outbound:asia"]["outputs"] == ["legs/outbound/sweep-asia.json"]
    assert sweeps_by_id["sweep:outbound:asia"]["command"][-1] == "outbound:asia"
    assert sweeps_by_id["sweep:return"]["outputs"] == ["legs/return/sweep.json"]
    assert sweeps_by_id["sweep:return"]["command"][-1] == "return"


# --- three-intent chain: a middle leg sweeps from its predecessor's reached dests ---


@pytest.fixture
def chain_trip(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "legs": [
                    {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
                    {"id": "hop", "dests": ["OKA"]},
                    {"id": "return", "dests": "$origins"},
                ]
            }
        ),
    )
    trips.artifact_write(
        SLUG,
        "legs/outbound/shortlist.json",
        json.dumps(
            shortlist_doc(
                [
                    {"dest": "NRT", "date": "2026-09-05"},
                    {"dest": "HND", "date": "2026-09-06"},
                ],
                considered=2,
            )
        ),
    )
    return SLUG


@respx.mock
def test_middle_leg_sweeps_from_predecessor_reached_dests(chain_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(chain_trip, "hop", now=clock())
    params = route.calls[0].request.url.params
    # The hop departs the outbound shortlist's reached dests (NRT, HND) toward its own dests (OKA).
    assert set(params["origin_airport"].split(",")) == {"NRT", "HND"}
    assert params["destination_airport"] == "OKA"


# --- per-intent window derivation: absolute vs chained ---


@respx.mock
def test_absolute_leg_window_overrides_trip_window(search_trip: str) -> None:
    trips.set_patch(
        search_trip,
        make_trip(
            {
                "legs": [
                    {
                        "origins": ["SFO"],
                        "buckets": [{"name": "asia", "dests": ["NRT"]}],
                        "window": {"start": "2026-10-01", "end": "2026-10-05"},
                    }
                ]
            }
        ),
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(search_trip, "outbound:asia", now=clock())
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-10-01"  # absolute per-intent window, no padding
    assert params["end_date"] == "2026-10-05"


@respx.mock
def test_chained_return_window_uses_trip_window(round_trip: str) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(round_trip, "return", now=clock())
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-09-01"  # return side: trip start, end padded
    assert params["end_date"] == "2026-09-21"


# --- chained-leg guards: empty predecessor + stay-shifted window ---


def _chain_plan(outbound_extra: dict | None = None) -> dict:
    outbound = {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]}
    outbound.update(outbound_extra or {})
    return {
        "legs": [
            outbound,
            {"id": "hop", "dests": ["OKA"]},
            {"id": "return", "dests": "$origins"},
        ]
    }


@respx.mock
def test_chained_sweep_empty_predecessor_raises_nodata_without_http(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_chain_plan()))
    trips.artifact_write(
        SLUG, "legs/outbound/shortlist.json", json.dumps(shortlist_doc([], considered=0))
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(NoData):
        sweeps.run(SLUG, "hop", now=clock())
    assert route.call_count == 0  # empty predecessor: no gateway, zero HTTP, walker backoff


DISCOVER_PLAN = {
    "legs": [
        {
            "origins": ["SFO"],
            "dests": {"discover": {"brief": "warm asian beach hubs", "max_airports": 6}},
            "mode": "award",
        }
    ]
}


@respx.mock
def test_discover_sweep_empty_scout_raises_nodata_without_http(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(DISCOVER_PLAN))
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(NoData):  # scout not yet written: no dest endpoints, zero HTTP
        sweeps.run(SLUG, "outbound", now=clock())
    assert route.call_count == 0


@respx.mock
def test_discover_sweep_endpoints_come_from_scout_artifact(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(DISCOVER_PLAN))
    trips.artifact_write(
        SLUG,
        "legs/outbound/scout.json",
        json.dumps([{"airport": "NRT", "why": "peak J space"}, {"airport": "BKK", "why": "cheap"}]),
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(SLUG, "outbound", now=clock())
    params = route.calls[0].request.url.params
    assert params["origin_airport"] == "SFO"
    assert params["destination_airport"] == "BKK,NRT"  # scout airports, sorted, feed the endpoints


def _discover_plan(max_airports: int) -> dict:
    discover = {"discover": {"brief": "warm asian beach hubs", "max_airports": max_airports}}
    return {"legs": [{"origins": ["SFO"], "dests": discover, "mode": "award"}]}


def _scout_doc(*airports: str) -> str:
    return json.dumps([{"airport": a, "why": "hub"} for a in airports])


@respx.mock
def test_discover_sweep_stale_over_cap_scout_raises_usage_error_without_http(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-G: lowering the cap under a written scout is caught at read time, spending no quota.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_discover_plan(5)))
    trips.artifact_write(
        SLUG, "legs/outbound/scout.json", _scout_doc("BKK", "NRT", "SIN", "KUL", "DPS")
    )
    trips.set_patch(SLUG, make_trip(_discover_plan(2)))
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(
        UsageError, match=r"lists 5 airports, over the leg's max_airports 2.*re-run scout"
    ):
        sweeps.run(SLUG, "outbound", now=clock())
    assert route.call_count == 0


@respx.mock
def test_discover_sweep_raised_cap_leaves_valid_scout_untouched(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cap raise leaves a still-valid scout working untouched.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_discover_plan(2)))
    trips.artifact_write(SLUG, "legs/outbound/scout.json", _scout_doc("NRT", "BKK"))
    trips.set_patch(SLUG, make_trip(_discover_plan(5)))
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(SLUG, "outbound", now=clock())
    assert route.calls[0].request.url.params["destination_airport"] == "BKK,NRT"


@respx.mock
def test_discover_dests_made_concrete_leaves_stale_scout_unread(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # dests turned concrete: no scout node compiles and the stale scout.json is never consulted.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_discover_plan(5)))
    trips.artifact_write(
        SLUG, "legs/outbound/scout.json", _scout_doc("BKK", "NRT", "SIN", "KUL", "DPS")
    )
    trips.set_patch(
        SLUG, make_trip({"legs": [{"origins": ["SFO"], "dests": ["HND"], "mode": "award"}]})
    )
    graph = trips.compile_graph(SLUG)
    assert "scout:outbound" not in {n["id"] for n in graph["nodes"]}
    sweep = next(n for n in graph["nodes"] if n["id"] == "sweep:outbound")
    assert sweep["inputs"] == []
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(SLUG, "outbound", now=clock())
    assert route.calls[0].request.url.params["destination_airport"] == "HND"


@respx.mock
def test_discover_sweep_malformed_scout_raises_usage_error_without_http(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-G read/write parity: a corrupt scout.json raises the typed parse UsageError, never a raw
    # JSONDecodeError, and spends zero quota.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_discover_plan(6)))
    path = trips._artifact_path(SLUG, "legs/outbound/scout.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(UsageError, match=r"failed to parse.*re-run scout"):
        sweeps.run(SLUG, "outbound", now=clock())
    assert route.call_count == 0


@respx.mock
def test_discover_sweep_zero_byte_scout_raises_usage_error_not_nodata(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0 bytes is corruption, not an empty scout — the write boundary only produces valid JSON ([]).
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_discover_plan(6)))
    path = trips._artifact_path(SLUG, "legs/outbound/scout.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(UsageError, match=r"failed to parse.*re-run scout"):
        sweeps.run(SLUG, "outbound", now=clock())
    assert route.call_count == 0


@respx.mock
def test_discover_sweep_undecodable_scout_raises_usage_error_without_http(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-G read/write parity: non-UTF-8 bytes fail at read_text() before the JSON parse — the same
    # typed parse UsageError, never a raw UnicodeDecodeError, and zero quota.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_discover_plan(6)))
    path = trips._artifact_path(SLUG, "legs/outbound/scout.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff")
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    with pytest.raises(UsageError, match=r"failed to parse.*re-run scout"):
        sweeps.run(SLUG, "outbound", now=clock())
    assert route.call_count == 0


@respx.mock
def test_discover_leg_with_buckets_unions_bucket_and_scout(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-H: the bucket (HND) and the scout-fed bare sweep (NRT) are both queried and both feed the
    # leg's shortlist — scout adds endpoints beside the bucket, never gates it.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "dests": {"discover": {"brief": "warm asian beach hubs", "max_airports": 6}},
                "mode": "award",
                "buckets": [{"name": "asia", "dests": ["HND"]}],
            }
        ]
    }
    trips.set_patch(SLUG, make_trip(plan))
    trips.artifact_write(SLUG, "legs/outbound/scout.json", _scout_doc("NRT"))

    def _row_for(request: httpx.Request) -> httpx.Response:
        dest = request.url.params["destination_airport"]
        return ok([biz(dest, dest=dest)])

    route = respx.get(SEARCH_URL).mock(side_effect=_row_for)
    sweeps.run(SLUG, "outbound:asia", now=clock())  # bucket sweep queries HND
    sweeps.run(SLUG, "outbound", now=clock())  # scout-fed bare sweep queries NRT
    queried = {call.request.url.params["destination_airport"] for call in route.calls}
    assert queried == {"HND", "NRT"}  # both endpoints queried side by side
    doc = shortlist.shortlist(SLUG, now=clock())
    assert {c["dest"] for c in doc["candidates"]} == {"HND", "NRT"}  # the union feeds the shortlist


@respx.mock
def test_middle_leg_stay_nights_shifts_sweep_window(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, make_trip(_chain_plan({"stay_nights": {"min": 2, "max": 3}})))
    trips.artifact_write(
        SLUG,
        "legs/outbound/shortlist.json",
        json.dumps(
            shortlist_doc(
                [
                    {"dest": "NRT", "date": "2026-09-12"},
                    {"dest": "NRT", "date": "2026-09-13"},
                ],
                considered=2,
            )
        ),
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(SLUG, "hop", now=clock())
    params = route.calls[0].request.url.params
    # Arrivals 09-12/09-13, stay {2,3} -> window spans [09-12+2 .. 09-13+3] = 09-14 .. 09-16.
    assert params["start_date"] == "2026-09-14"
    assert params["end_date"] == "2026-09-16"


@respx.mock
def test_open_jaw_override_origins_replace_chained_gateways(chain_trip: str) -> None:
    # Contrast pin for the pairs lane: an open-jaw override REPLACES the chained gateways here too —
    # the hop departs EXACTLY its explicit origins, not the predecessor's reached dests.
    trips.set_patch(
        chain_trip,
        make_trip(
            {
                "legs": [
                    {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
                    {"id": "hop", "origins": ["KIX"], "dests": ["OKA"]},
                    {"id": "return", "dests": "$origins"},
                ]
            }
        ),
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(chain_trip, "hop", now=clock())
    params = route.calls[0].request.url.params
    assert params["origin_airport"] == "KIX"  # override replaces NRT/HND from the chain
    assert params["destination_airport"] == "OKA"


@respx.mock
def test_optional_middle_leg_return_sweeps_from_both_boundaries(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-A live: with the hop optional, the return sweep departs the hop's landings (present) AND the
    # pre-optional outbound landings (hop skipped) — both markets searched in one node.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "legs": [
                    {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
                    {"id": "hop", "dests": ["OKA"], "optional": True},
                    {"id": "return", "dests": "$origins"},
                ]
            }
        ),
    )
    trips.artifact_write(
        SLUG, "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([{"dest": "NRT", "date": "2026-09-05"}], considered=1)),
    )
    trips.artifact_write(
        SLUG, "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([{"dest": "OKA", "date": "2026-09-10"}], leg="hop", considered=1)),
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(SLUG, "return", now=clock())
    params = route.calls[0].request.url.params
    assert set(params["origin_airport"].split(",")) == {"OKA", "NRT"}  # both boundaries
    assert params["destination_airport"] == "SFO"  # home


@respx.mock
def test_optional_middle_leg_sweep_window_envelopes_pre_boundary_stay(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-A window union (MAJOR-2): with hop1 skipped, hop2 chains from the pre-boundary outbound
    # whose wider stay departs LATER — the window end envelopes it (the refuter's 09-08 is swept).
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        make_trip(
            {
                "legs": [
                    {"origins": ["SFO"], "dests": ["NRT"], "stay_nights": {"min": 2, "max": 6}},
                    {"id": "hop1", "dests": ["OKA"], "optional": True,
                     "stay_nights": {"min": 1, "max": 1}},
                    {"id": "hop2", "dests": ["SIN"]},
                    {"id": "return", "dests": "$origins"},
                ]
            }
        ),
    )
    trips.artifact_write(
        SLUG, "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([{"dest": "NRT", "date": "2026-09-04"}], considered=1)),
    )
    trips.artifact_write(
        SLUG, "legs/hop1/shortlist.json",
        json.dumps(shortlist_doc([{"dest": "OKA", "date": "2026-09-05"}], leg="hop1")),
    )
    route = respx.get(SEARCH_URL).mock(return_value=ok([]))
    sweeps.run(SLUG, "hop2", now=clock())
    params = route.calls[0].request.url.params
    assert set(params["origin_airport"].split(",")) == {"OKA", "NRT"}  # both boundaries
    # full (hop1 09-05 + 1) = [09-06,09-06]; skip (outbound 09-04 + {2..6}) = [09-06,09-10]
    assert params["start_date"] == "2026-08-25"  # start floored to padded trip window (09-01 - 7d)
    assert params["end_date"] == "2026-09-10"
    assert params["start_date"] <= "2026-09-08" <= params["end_date"]


# --- R-E: removing optional legs keeps a superset of every variant's sweep coverage ---

RE_WINDOW = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}
RE_ARRIVALS = {
    "outbound": "2026-09-04", "onward": "2026-09-06", "hop": "2026-09-08",
    "hop1": "2026-09-05", "hop2": "2026-09-09",
    # A late first-leg arrival whose stay-shift lands past the padded trip-window end — so the
    # fall-through (no-stay / empty-shortlist) cases exercise the truncation, not a no-op window.
    "ob": "2026-09-12", "mid": "2026-09-13", "tail": "2026-09-13",
}
RE_LEADING = {
    "legs": [
        {"id": "pos", "origins": ["SFO"], "dests": ["LAX"], "mode": "cash", "optional": True},
        {"id": "onward", "dests": ["NRT"], "mode": "award"},
    ]
}
RE_OPEN_JAW = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
        {"id": "hop", "origins": ["KIX"], "dests": ["BKK"], "mode": "award", "optional": True},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
RE_STAY = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "dests": ["NRT"], "mode": "award",
         "stay_nights": {"min": 2, "max": 6}},
        {"id": "hop1", "dests": ["OKA"], "mode": "award", "optional": True,
         "stay_nights": {"min": 1, "max": 1}},
        {"id": "hop2", "dests": ["SIN"], "mode": "award"},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
RE_CONSECUTIVE = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
        {"id": "hop1", "dests": ["BKK"], "mode": "award", "optional": True},
        {"id": "hop2", "dests": ["SIN"], "mode": "award", "optional": True},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
RE_NOSTAY = {  # (e) a no-stay optional before a middle leg: the successor falls through the stay
    "legs": [  # branch, so the padded-window base must still envelope the pre-boundary skip window.
        {"id": "ob", "origins": ["SFO"], "dests": ["NRT"], "mode": "award",
         "stay_nights": {"min": 2, "max": 12}},
        {"id": "mid", "dests": ["OKA"], "mode": "award", "optional": True},
        {"id": "tail", "dests": ["SIN"], "mode": "award"},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
RE_EMPTYHOP = {  # (f) a stay-carrying optional with an EMPTY shortlist: observed arrivals are empty
    "legs": [  # so the stay branch's inner guard fails and the successor falls through identically.
        {"id": "ob", "origins": ["SFO"], "dests": ["NRT"], "mode": "award",
         "stay_nights": {"min": 2, "max": 12}},
        {"id": "dead", "dests": ["OKA"], "mode": "award", "optional": True,
         "stay_nights": {"min": 1, "max": 1}},
        {"id": "tail", "dests": ["SIN"], "mode": "award"},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
RE_CASH = {  # (g) a cash onward after an optional leg: the cash lane must price the skip variant's
    "legs": [  # stay-shifted gateway dates beyond the trip-window end, not the raw trip window.
        {"id": "ob", "origins": ["SFO"], "dests": ["NRT"], "mode": "award",
         "stay_nights": {"min": 2, "max": 12}},
        {"id": "hop", "dests": ["OKA"], "mode": "award", "optional": True},
        {"id": "onward", "dests": ["BKK"], "mode": "cash"},
    ]
}
# A confirmed outbound window poking past the padded trip window (pad 7 → 08-25..09-21): the skipped
# leading optional promotes its successor to FIRST, where the hard window is authoritative.
RE_OUTBOUND = {"outbound_departure_window": {"start": "2026-08-20", "end": "2026-09-25",
                                             "confirmed": True}}
RE_CONSTRAINT = {  # (h) a leading optional under a confirmed outbound window past the padded end —
    "constraints": RE_OUTBOUND,  # the search successor's skip source must envelope the hard window.
    "legs": [
        {"id": "pos", "origins": ["SFO"], "dests": ["LAX"], "mode": "cash", "optional": True},
        {"id": "onward", "dests": ["NRT"], "mode": "award"},
    ],
}
RE_PROGRAM = {  # (i) a program_sweeps successor of a leading optional: the availability branch must
    "constraints": RE_OUTBOUND,  # thread the same skip envelope through the promoted-first window.
    "legs": [
        {"id": "pos", "origins": ["SFO"], "dests": ["LAX"], "mode": "cash", "optional": True},
        {"id": "onward", "mode": "award",
         "program_sweeps": [{"source": "aeroplan", "dest_region": "Asia"}]},
    ],
}


def _re_setup(slug: str, plan: dict, empty: frozenset[str] = frozenset()) -> dict:
    """Compile a plan and seed each award leg's shortlist with one candidate per declared dest at
    that leg's arrival date — an EMPTY shortlist for legs in ``empty`` (a dead/unsearched optional
    market) — so both variants derive from identical seeded artifacts. Returns the trip doc."""
    trips.new(slug, now=clock())
    trips.set_patch(slug, {"window": RE_WINDOW, "cabin": "business", "plan": plan})
    legs = trips.show(slug)["plan"]["legs"]
    for leg in legs:
        if leg["mode"] not in ("award", "either") or not isinstance(leg.get("dests"), list):
            continue
        cands = (
            []
            if leg["id"] in empty
            else [{"dest": dest, "date": RE_ARRIVALS[leg["id"]]} for dest in leg["dests"]]
        )
        trips.artifact_write(
            slug, f"legs/{leg['id']}/shortlist.json",
            json.dumps(shortlist_doc(cands, leg=leg["id"])),
        )
    return trips.show(slug)


def _re_seed(slug: str, plan: dict, empty: frozenset[str] = frozenset()) -> dict[str, dict]:
    """Resolve every sweep leg (origins + window) of a seeded plan so chained coverage and stay-
    shifted windows derive deterministically from identical seeded artifacts on both sides."""
    trip = _re_setup(slug, plan, empty)
    resolved: dict[str, dict] = {}
    for node in trips.compile_graph(slug)["nodes"]:
        if node["id"].startswith("sweep:"):
            key = node["id"].removeprefix("sweep:")
            resolved[key.partition(":")[0]] = sweeps._leg_for_key(
                slug, key, trip, node["endpoint_source"]
            )
    return resolved


def _re_gateway_dates(slug: str, plan: dict, cash_leg: str) -> dict[str, set[str]]:
    """A cash leg's compiled pairs-node gateway dates from a seeded plan — the cash-lane analogue of
    :func:`_re_seed`'s sweep resolve, over identical seeded artifacts."""
    trip = _re_setup(slug, plan)
    node = next(n for n in trips.compile_graph(slug)["nodes"] if n["id"] == f"pairs:{cash_leg}")
    leg_intent = next(leg for leg in trip["plan"]["legs"] if leg["id"] == cash_leg)
    return shortlist._gateway_dates(
        slug, node, leg_intent, trip, sweeps._predecessor(trip["plan"], cash_leg)
    )


def _re_variant(plan: dict, remove: set[str]) -> dict:
    home = plan["legs"][0]["origins"]  # a skipped leading leg leaves its successor departing home
    legs = [dict(leg) for leg in plan["legs"] if leg["id"] not in remove]
    if "origins" not in legs[0]:
        legs[0] = {**legs[0], "origins": home}
    return {**plan, "legs": legs}


@pytest.mark.parametrize(
    ("plan", "remove", "shared", "empty"),
    [
        pytest.param(
            RE_LEADING, {"pos"}, {"onward"}, frozenset(), id="leading-optional-positioning"
        ),
        pytest.param(
            RE_OPEN_JAW, {"hop"}, {"outbound", "return"}, frozenset(),
            id="mid-optional-declared-origins",
        ),
        pytest.param(
            RE_STAY, {"hop1"}, {"outbound", "hop2", "return"}, frozenset(),
            id="pre-boundary-wide-stay",
        ),
        pytest.param(
            RE_CONSECUTIVE, {"hop1", "hop2"}, {"outbound", "return"}, frozenset(),
            id="consecutive-optionals",
        ),
        pytest.param(
            RE_NOSTAY, {"mid"}, {"ob", "tail", "return"}, frozenset(),
            id="no-stay-optional-before-middle",
        ),
        pytest.param(
            RE_EMPTYHOP, {"dead"}, {"ob", "tail", "return"}, frozenset({"dead"}),
            id="stay-optional-empty-shortlist",
        ),
        pytest.param(
            RE_CONSTRAINT, {"pos"}, {"onward"}, frozenset(), id="leading-optional-outbound-window",
        ),
        pytest.param(
            RE_PROGRAM, {"pos"}, {"onward"}, frozenset(), id="program-sweep-successor-of-optional",
        ),
    ],
)
def test_removing_optional_legs_keeps_superset_sweep_coverage(
    getaway_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan: dict,
    remove: set[str],
    shared: set[str],
    empty: frozenset[str],
) -> None:
    # R-E: the full plan's compiled sweep coverage (origin sets AND date windows) is a SUPERSET of
    # what compile_graph(variant) derives for every leg shared with the optional-removed variant.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    full = _re_seed("2026-09-re-full", plan, empty)
    variant = _re_seed("2026-09-re-variant", _re_variant(plan, remove), empty)
    assert set(variant) == shared  # non-vacuous: every shared leg is actually compared
    for leg_id, v_leg in variant.items():
        f_leg = full[leg_id]
        if "origins" in v_leg:  # availability legs query by region, not concrete origins
            assert set(v_leg["origins"]) <= set(f_leg["origins"]), (
                f"{leg_id}: full origins {f_leg['origins']} omit variant origins {v_leg['origins']}"
            )
        (f_start, f_end), (v_start, v_end) = f_leg["window"], v_leg["window"]
        assert f_start <= v_start and f_end >= v_end, (
            f"{leg_id}: full window {f_leg['window']} misses variant window {v_leg['window']}"
        )


def test_removing_optional_leg_keeps_superset_cash_gateway_dates(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # R-E (g): a cash onward's compiled gateway dates in the full plan are a SUPERSET, per shared
    # gateway, of the variant's — the skip variant's stay-shifted NRT dates (past the window end).
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    prefs.init()
    full = _re_gateway_dates("2026-09-re-cash-full", RE_CASH, "onward")
    variant = _re_gateway_dates("2026-09-re-cash-variant", _re_variant(RE_CASH, {"hop"}), "onward")
    assert variant["NRT"] == shortlist._dates_between("2026-09-14", "2026-09-24")  # 09-12 + {2..12}
    assert max(variant["NRT"]) > "2026-09-14"  # the skip dates truly overrun the raw trip window
    for gateway, v_dates in variant.items():
        assert v_dates <= full[gateway], (
            f"{gateway}: full omits variant dates {sorted(v_dates - full.get(gateway, set()))}"
        )
