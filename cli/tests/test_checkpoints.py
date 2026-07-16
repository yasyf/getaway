import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import expand_doc, shortlist_doc, sweep_envelope
from click.testing import CliRunner

from getaway import enhance, factors, prefs, trips
from getaway.paths import UsageError

SLUG = "2026-09-asia-business"
BASE = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
WINDOW = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}
OCTOBER = {"start": "2026-10-01", "end": "2026-10-14", "trip_length_days": 10}
OUTBOUND_LEG = {
    "origins": ["SFO"],
    "mode": "award",
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
}
ROUND_TRIP = {"legs": [OUTBOUND_LEG, {"id": "return", "dests": "$origins", "mode": "award"}]}
ONE_WAY = {"legs": [OUTBOUND_LEG]}


def at(hours: float) -> Callable[[], dt.datetime]:
    moment = BASE + dt.timedelta(hours=hours)
    return lambda: moment


def _write(slug: str, name: str, doc: object) -> None:
    trips.artifact_write(slug, name, json.dumps(doc))


def _verify_row(target_id: str) -> dict:
    return {
        "target_id": target_id,
        "outcome": "gone",
        "checked_at": "2026-07-13T14:32:00+00:00",
        "method": "cookie",
        "observed": None,
        "evidence": "live-site check",
    }


@pytest.fixture
def trip(getaway_home: Path) -> str:
    prefs.init()
    prefs.set_balance("aeroplan", 50000)
    trips.new(SLUG, now=at(0))
    trips.set_patch(
        SLUG,
        {"cabin": "business", "window": WINDOW, "plan": ROUND_TRIP},
    )
    # The artifacts the downstream nodes declare as inputs.
    _write(SLUG, "legs/outbound/sweep-asia.json", sweep_envelope())
    _write(SLUG, "legs/outbound/shortlist.json", shortlist_doc())
    _write(SLUG, "legs/return/shortlist.json", shortlist_doc(leg="return"))
    _write(SLUG, "expand.json", expand_doc())
    _write(SLUG, "assess.json", {"journeys": {}, "notable_stretches": []})
    return SLUG


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_phase_check_missing_record(trip: str) -> None:
    fresh, record = trips.phase_check(trip, "rank")
    assert fresh is False
    assert record is None


def test_phase_done_records_fields(trip: str) -> None:
    record = trips.phase_done(trip, "rank", quota_after=42, now=at(0))
    assert record["quota_after"] == 42
    assert record["completed_at"] == "2026-07-13T12:00:00+00:00"
    assert record["inputs_fp"] is not None
    assert record["upstream_fp"] is not None  # rank declares shortlist/expand/assess inputs
    assert "artifacts" not in record
    stored = json.loads((trips.trip_dir(trip) / "checkpoints.json").read_text())
    assert stored["rank"] == record


def test_window_edit_invalidates_all_phases(trip: str) -> None:
    trips.phase_done(trip, "shortlist:outbound", now=at(0))
    trips.phase_done(trip, "rank", now=at(0))
    assert trips.phase_fresh(trip, "shortlist:outbound", now=at(1))
    assert trips.phase_fresh(trip, "rank", now=at(1))
    trips.set_patch(trip, {"window": OCTOBER})
    assert not trips.phase_fresh(trip, "shortlist:outbound", now=at(1))
    assert not trips.phase_fresh(trip, "rank", now=at(1))


def test_prefs_avoid_edit_invalidates_all_phases(trip: str) -> None:
    trips.phase_done(trip, "shortlist:outbound", now=at(0))
    trips.phase_done(trip, "rank", now=at(0))
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    assert not trips.phase_fresh(trip, "shortlist:outbound", now=at(1))
    assert not trips.phase_fresh(trip, "rank", now=at(1))


def test_balance_edit_invalidates_only_rank_and_finalize(trip: str) -> None:
    trips.phase_done(trip, "shortlist:outbound", now=at(0))
    trips.phase_done(trip, "rank", now=at(0))
    trips.phase_done(trip, "finalize", now=at(0))
    prefs.set_balance("aeroplan", 999999)
    assert trips.phase_fresh(trip, "shortlist:outbound", now=at(1))
    assert not trips.phase_fresh(trip, "rank", now=at(1))
    assert not trips.phase_fresh(trip, "finalize", now=at(1))


def test_artifact_content_change_invalidates_dependent_node(trip: str) -> None:
    trips.phase_done(trip, "shortlist:outbound", now=at(0))  # depends on sweep-asia.json
    trips.phase_done(trip, "expand", now=at(0))  # depends on the shortlists, not sweep-asia
    assert trips.phase_fresh(trip, "shortlist:outbound", now=at(1))
    assert trips.phase_fresh(trip, "expand", now=at(1))
    row = {
        "ID": "R9",
        "Route": {"OriginAirport": "SFO", "DestinationAirport": "NRT"},
        "Date": "2026-09-05",
        "Source": "united",
    }
    _write(trip, "legs/outbound/sweep-asia.json", sweep_envelope([row]))
    assert not trips.phase_fresh(trip, "shortlist:outbound", now=at(1))
    assert trips.phase_fresh(trip, "expand", now=at(1))


def test_ttl_expiry_marks_sweep_stale(trip: str) -> None:
    trips.phase_done(trip, "sweep:outbound:asia", now=at(0))
    assert trips.phase_fresh(trip, "sweep:outbound:asia", now=at(23))
    assert not trips.phase_fresh(trip, "sweep:outbound:asia", now=at(25))


def test_derived_node_never_expires_by_time(trip: str) -> None:
    trips.phase_done(trip, "rank", now=at(0))  # rank has no TTL
    assert trips.phase_fresh(trip, "rank", now=at(10000))


def test_absent_input_arrival_flips_stale(getaway_home: Path) -> None:
    prefs.init()
    trips.new("solo", now=at(0))
    trips.set_patch(
        "solo",
        {"cabin": "business", "window": WINDOW, "plan": ONE_WAY},
    )
    # shortlist:outbound depends on legs/outbound/sweep-asia.json, which does not exist yet.
    trips.phase_done("solo", "shortlist:outbound", now=at(0))
    assert trips.phase_fresh("solo", "shortlist:outbound", now=at(1))
    _write("solo", "legs/outbound/sweep-asia.json", sweep_envelope())
    assert not trips.phase_fresh("solo", "shortlist:outbound", now=at(1))


def test_unknown_node_id_rejected(trip: str) -> None:
    with pytest.raises(UsageError, match="unknown node id"):
        trips.phase_done(trip, "sweep:eur", now=at(0))


def test_removed_node_reads_stale_after_plan_change(trip: str) -> None:
    trips.phase_done(trip, "sweep:return", now=at(0))
    assert trips.phase_fresh(trip, "sweep:return", now=at(1))
    trips.set_patch(trip, {"plan": ONE_WAY})  # drops the return leg
    fresh, record = trips.phase_check(trip, "sweep:return", now=at(1))
    assert fresh is False
    assert record is not None  # the checkpoint survives; the phase is simply no longer applicable


def test_phase_check_cli_exits_one_when_stale(trip: str, runner: CliRunner) -> None:
    trips.phase_done(trip, "sweep:outbound:asia", now=at(0))
    trips.set_patch(trip, {"window": OCTOBER})
    result = runner.invoke(trips.trip_group, ["phase-check", trip, "sweep:outbound:asia"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["fresh"] is False


def test_enhance_merge_flips_only_rank_and_finalize(trip: str) -> None:
    # rank and finalize declare enhance-verify.json as an input; nothing upstream does. A merge
    # write flips exactly those two stale while expand and assess stay fresh.
    for node_id in ("expand", "assess", "rank", "finalize"):
        trips.phase_done(trip, node_id, now=at(0))
    for node_id in ("expand", "assess", "rank", "finalize"):
        assert trips.phase_fresh(trip, node_id, now=at(1))
    enhance.merge(
        trip,
        "verify",
        [
            {
                "target_id": "AV1:J",
                "outcome": "gone",
                "checked_at": "2026-07-13T14:32:00+00:00",
                "method": "cookie",
                "observed": None,
                "evidence": "live-site check",
            }
        ],
    )
    assert trips.phase_fresh(trip, "expand", now=at(1))
    assert trips.phase_fresh(trip, "assess", now=at(1))
    assert not trips.phase_fresh(trip, "rank", now=at(1))
    assert not trips.phase_fresh(trip, "finalize", now=at(1))


def test_rank_folds_out_a_merge_that_lands_mid_run(
    trip: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A merge between rank's input read and its checkpoint stamp must leave rank stale, not masked.
    enhance.merge(trip, "verify", [_verify_row("seed:J")])  # seed activates availability_verified
    real = enhance.results_index

    def racing(slug: str, name: str) -> dict:
        enhance.merge(slug, "verify", [_verify_row("late:J")])  # lands between read and stamp
        return real(slug, name)

    monkeypatch.setattr(enhance, "results_index", racing)
    factors.rank(trip, now=at(0))
    fresh, record = trips.phase_check(trip, "rank", now=at(1))
    assert record is not None
    assert fresh is False


def test_captured_upstream_fp_marks_rank_stale_after_a_later_merge(trip: str) -> None:
    enhance.merge(trip, "verify", [_verify_row("seed:J")])
    captured = trips.capture_upstream_fp(trip, "rank")
    enhance.merge(trip, "verify", [_verify_row("late:J")])  # enhance-verify.json bytes change
    trips.phase_done(trip, "rank", now=at(0), upstream_fp=captured)
    fresh, record = trips.phase_check(trip, "rank", now=at(1))
    assert record is not None
    assert fresh is False


def test_rank_captures_inputs_fp_before_a_mid_run_prefs_edit(
    trip: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A prefs edit between rank's input read and its checkpoint stamp must leave rank stale, not
    # stamp the post-edit fingerprint over rows derived from the pre-edit balances.
    real = factors._order

    def racing(
        entries: list[dict], tiers: dict, active: set[str], primary_codes: frozenset[str]
    ) -> list[dict]:
        prefs.set_balance("aeroplan", 999999)  # lands between read and stamp
        return real(entries, tiers, active, primary_codes)

    monkeypatch.setattr(factors, "_order", racing)
    factors.rank(trip, now=at(0))
    fresh, record = trips.phase_check(trip, "rank", now=at(1))
    assert record is not None
    assert fresh is False


def test_finalize_captures_inputs_fp_before_a_mid_run_prefs_edit(
    trip: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    factors.rank(trip, now=at(0))  # finalize reads rank.json
    real = factors._thread_verification

    def racing(doc: dict, slug: str) -> None:
        prefs.set_balance("aeroplan", 999999)  # lands between read and stamp
        real(doc, slug)

    monkeypatch.setattr(factors, "_thread_verification", racing)
    factors.finalize(trip, now=at(0))
    fresh, record = trips.phase_check(trip, "finalize", now=at(1))
    assert record is not None
    assert fresh is False
