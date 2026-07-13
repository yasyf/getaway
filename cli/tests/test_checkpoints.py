import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from getaway import prefs, trips

SLUG = "2026-09-asia-business"
BASE = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def at(hours: float) -> Callable[[], dt.datetime]:
    moment = BASE + dt.timedelta(hours=hours)
    return lambda: moment


@pytest.fixture
def trip(getaway_home: Path) -> str:
    prefs.init()
    prefs.set_balance("aeroplan", 50000)
    trips.new(SLUG, now=at(0))
    trips.set_patch(
        SLUG, {"window": {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}}
    )
    trips.artifact_write(SLUG, "sweep.jsonl", '{"route": "SFO-BKK"}\n')
    trips.artifact_write(SLUG, "rank.json", "{}")
    return SLUG


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_phase_check_missing_record(trip: str) -> None:
    fresh, record = trips.phase_check(trip, "never-run")
    assert fresh is False
    assert record is None


def test_phase_done_records_fields(trip: str) -> None:
    record = trips.phase_done(trip, "sweep:sea-asia", ["sweep.jsonl"], quota_after=42, now=at(0))
    assert record["artifacts"] == ["sweep.jsonl"]
    assert record["quota_after"] == 42
    assert record["completed_at"] == "2026-07-13T12:00:00+00:00"
    assert record["upstream_fp"] is not None
    stored = json.loads((trips.trip_dir(trip) / "checkpoints.json").read_text())
    assert stored["sweep:sea-asia"] == record


def test_window_edit_invalidates_all_phases(trip: str) -> None:
    trips.phase_done(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(0))
    trips.phase_done(trip, "rank", [], now=at(0))
    assert trips.phase_check(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(1))[0]
    assert trips.phase_check(trip, "rank", None, now=at(1))[0]
    trips.set_patch(
        trip, {"window": {"start": "2026-10-01", "end": "2026-10-14", "trip_length_days": 10}}
    )
    assert not trips.phase_check(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(1))[0]
    assert not trips.phase_check(trip, "rank", None, now=at(1))[0]


def test_prefs_avoid_edit_invalidates_all_phases(trip: str) -> None:
    trips.phase_done(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(0))
    trips.phase_done(trip, "rank", [], now=at(0))
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    assert not trips.phase_check(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(1))[0]
    assert not trips.phase_check(trip, "rank", None, now=at(1))[0]


def test_balance_edit_invalidates_only_rank_and_finalize(trip: str) -> None:
    trips.phase_done(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(0))
    trips.phase_done(trip, "rank", [], now=at(0))
    trips.phase_done(trip, "finalize", [], now=at(0))
    prefs.set_balance("aeroplan", 999999)
    assert trips.phase_check(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(1))[0]
    assert not trips.phase_check(trip, "rank", None, now=at(1))[0]
    assert not trips.phase_check(trip, "finalize", None, now=at(1))[0]


def test_artifact_content_change_invalidates_dependent_phase(trip: str) -> None:
    trips.phase_done(trip, "shortlist", ["sweep.jsonl"], now=at(0))
    trips.phase_done(trip, "evidence.cash", ["rank.json"], now=at(0))
    assert trips.phase_check(trip, "shortlist", ["sweep.jsonl"], now=at(1))[0]
    assert trips.phase_check(trip, "evidence.cash", ["rank.json"], now=at(1))[0]
    trips.artifact_write(trip, "sweep.jsonl", '{"route": "SFO-SIN"}\n')
    assert not trips.phase_check(trip, "shortlist", ["sweep.jsonl"], now=at(1))[0]
    assert trips.phase_check(trip, "evidence.cash", ["rank.json"], now=at(1))[0]


def test_ttl_expiry_marks_phase_stale(trip: str) -> None:
    trips.phase_done(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(0))
    assert trips.phase_check(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(23))[0]
    assert not trips.phase_check(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(25))[0]


def test_colon_suffix_ttl_lookup_and_untimed_phase(trip: str) -> None:
    trips.phase_done(trip, "sweep:eur", ["sweep.jsonl"], now=at(0))
    # base "sweep" TTL (24h) governs the ":"-suffixed key
    assert trips.phase_check(trip, "sweep:eur", ["sweep.jsonl"], now=at(23))[0]
    assert not trips.phase_check(trip, "sweep:eur", ["sweep.jsonl"], now=at(25))[0]
    # "rank" is absent from the TTL map, so time never expires it
    trips.phase_done(trip, "rank", [], now=at(0))
    assert trips.phase_check(trip, "rank", None, now=at(10000))[0]


def test_phase_check_cli_exits_one_when_stale(trip: str, runner: CliRunner) -> None:
    trips.phase_done(trip, "sweep:sea-asia", ["sweep.jsonl"], now=at(0))
    trips.set_patch(
        trip, {"window": {"start": "2026-10-01", "end": "2026-10-14", "trip_length_days": 10}}
    )
    result = runner.invoke(
        trips.trip_group,
        ["phase-check", trip, "sweep:sea-asia", "--dep", "sweep.jsonl"],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["fresh"] is False
