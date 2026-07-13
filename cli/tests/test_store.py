import datetime as dt
import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.testing import CliRunner

from getaway import store
from getaway.constants import EXIT_NEGATIVE, EXIT_NO_DATA, EXIT_OK
from getaway.store import NoData, QuotaFloorError, Store

RUNNER = str(Path(__file__).parent / "_runner.py")

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def _clock(moment: dt.datetime) -> Callable[[], dt.datetime]:
    return lambda: moment


def make_row(
    row_id: str,
    origin: str,
    dest: str,
    date: str,
    source: str,
    cabins: dict[str, tuple[bool, str, int, str, bool]],
) -> dict:
    row = {
        "ID": row_id,
        "Route": {
            "OriginAirport": origin,
            "DestinationAirport": dest,
            "OriginRegion": "North America",
            "DestinationRegion": "Asia",
            "Distance": 5000,
            "Source": source,
        },
        "Date": date,
        "Source": source,
        "UpdatedAt": "2026-07-12T00:00:00Z",
    }
    for letter in ("Y", "W", "J", "F"):
        available, cost, seats, airlines, direct = cabins.get(letter, (False, "0", 0, "", False))
        row[f"{letter}Available"] = available
        row[f"{letter}MileageCost"] = cost
        row[f"{letter}RemainingSeats"] = seats
        row[f"{letter}Airlines"] = airlines
        row[f"{letter}Direct"] = direct
    return row


ROWS = [
    make_row(
        "R1",
        "SFO",
        "NRT",
        "2026-09-01",
        "united",
        {"Y": (True, "35000", 4, "UA", True), "J": (True, "80000", 2, "NH, UA", False)},
    ),
    make_row(
        "R2",
        "LAX",
        "NRT",
        "2026-09-05",
        "united",
        {"Y": (True, "40000", 1, "UA", False), "J": (True, "90000", 3, "NH", True)},
    ),
    make_row(
        "R3",
        "SFO",
        "HND",
        "2026-09-10",
        "aeroplan",
        {"Y": (True, "55000", 6, "AC", True), "J": (True, "110000", 2, "NH", True)},
    ),
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


@pytest.fixture
def seeded(db_path: Path) -> Store:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.ingest(ROWS)
    return st


def test_bootstrap_sets_application_id_and_version(db_path: Path) -> None:
    store.connect(db_path)
    conn = sqlite3.connect(str(db_path))
    assert conn.execute("PRAGMA application_id").fetchone()[0] == store.APPLICATION_ID
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.USER_VERSION
    conn.close()


def test_connect_creates_db_and_sidecars_0600(db_path: Path) -> None:
    st = store.connect(db_path)
    st.record_quota("/search", 100)  # a write forces the WAL sidecars into existence
    for suffix in ("", "-wal", "-shm"):
        created = Path(str(db_path) + suffix)
        assert created.exists(), suffix
        assert stat.S_IMODE(created.stat().st_mode) == 0o600, suffix


def test_connect_restores_widened_sidecar_modes(db_path: Path) -> None:
    st = store.connect(db_path)
    st.record_quota("/search", 100)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).chmod(0o644)
    store.connect(db_path)
    for suffix in ("-wal", "-shm"):
        assert stat.S_IMODE(Path(str(db_path) + suffix).stat().st_mode) == 0o600, suffix


def test_bootstrap_idempotent_preserves_rows(db_path: Path) -> None:
    first = store.connect(db_path, now=_clock(FROZEN))
    first.ingest(ROWS)
    second = store.connect(db_path, now=_clock(FROZEN))
    assert second.stats()["availability"] == 3


def test_user_version_mismatch_deletes_and_recreates(db_path: Path) -> None:
    seeded = store.connect(db_path, now=_clock(FROZEN))
    seeded.ingest(ROWS)
    tamper = sqlite3.connect(str(db_path))
    tamper.execute("PRAGMA user_version=999")
    tamper.commit()
    tamper.close()
    recreated = store.connect(db_path, now=_clock(FROZEN))
    assert recreated.stats()["availability"] == 0


def test_foreign_application_id_deletes_and_recreates(db_path: Path) -> None:
    foreign = sqlite3.connect(str(db_path))
    foreign.execute("CREATE TABLE junk (x INTEGER)")
    foreign.execute("PRAGMA application_id=305419896")
    foreign.commit()
    foreign.close()
    fresh = store.connect(db_path, now=_clock(FROZEN))
    assert fresh.stats()["availability"] == 0
    assert fresh.stats()["quota_events"] == 0


def test_concurrent_first_connect_serializes_bootstrap(db_path: Path) -> None:
    workers = 32
    barrier = threading.Barrier(workers)
    errors: list[Exception] = []

    def boot() -> None:
        barrier.wait()  # release all threads into connect() at once
        try:
            st = store.connect(db_path)
            st.record_quota("/search", 1)
        except Exception as err:  # noqa: BLE001 -- the race surfaces OperationalError
            errors.append(err)

    threads = [threading.Thread(target=boot) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.connect(db_path).stats()["quota_events"] == workers


def test_ingest_counts_new_rows(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    result = st.ingest(ROWS)
    assert result == {"rows": 3, "new": 3}


def test_reingest_updates_fetched_at_without_duplicating(db_path: Path) -> None:
    later = FROZEN + dt.timedelta(hours=3)
    first = store.connect(db_path, now=_clock(FROZEN))
    first.ingest([ROWS[0]])
    second = store.connect(db_path, now=_clock(later))
    result = second.ingest([ROWS[0]])
    assert result == {"rows": 1, "new": 0}
    assert second.stats()["availability"] == 1
    assert second.stats()["availability_cabin"] == 4
    conn = sqlite3.connect(str(db_path))
    fetched = conn.execute("SELECT fetched_at FROM availability WHERE id='R1'").fetchone()[0]
    conn.close()
    assert fetched == later.isoformat()


def test_ingest_records_sweep_membership(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    sweep = {
        "trip_slug": "warm-week",
        "label": "asia",
        "kind": "search",
        "params": {"origins": ["SFO"]},
        "started_at": FROZEN.isoformat(),
    }
    st.ingest(ROWS, sweep=sweep)
    assert st.stats(trip_slug="warm-week") == {"trip_slug": "warm-week", "sweeps": 1, "rows": 3}


def _ids(rows: list[dict]) -> set[tuple[str, str]]:
    return {(row["id"], row["cabin"]) for row in rows}


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param(
            {"origins": ["SFO"]},
            {
                ("R1", "Y"),
                ("R1", "W"),
                ("R1", "J"),
                ("R1", "F"),
                ("R3", "Y"),
                ("R3", "W"),
                ("R3", "J"),
                ("R3", "F"),
            },
            id="origins-filter",
        ),
        pytest.param(
            {"dests": ["HND"]},
            {("R3", "Y"), ("R3", "W"), ("R3", "J"), ("R3", "F")},
            id="dests-filter",
        ),
        pytest.param(
            {"date_start": "2026-09-05"},
            {
                ("R2", "Y"),
                ("R2", "W"),
                ("R2", "J"),
                ("R2", "F"),
                ("R3", "Y"),
                ("R3", "W"),
                ("R3", "J"),
                ("R3", "F"),
            },
            id="date-start-filter",
        ),
        pytest.param(
            {"date_end": "2026-09-01"},
            {("R1", "Y"), ("R1", "W"), ("R1", "J"), ("R1", "F")},
            id="date-end-filter",
        ),
        pytest.param(
            {"cabin": "J"},
            {("R1", "J"), ("R2", "J"), ("R3", "J")},
            id="cabin-filter",
        ),
        pytest.param(
            {"cabin": "J", "max_mileage": 90000},
            {("R1", "J"), ("R2", "J")},
            id="max-mileage-filter",
        ),
        pytest.param(
            {"cabin": "J", "min_seats": 3},
            {("R2", "J")},
            id="min-seats-filter",
        ),
        pytest.param(
            {"sources": ["aeroplan"]},
            {("R3", "Y"), ("R3", "W"), ("R3", "J"), ("R3", "F")},
            id="sources-filter",
        ),
        pytest.param(
            {"cabin": "J", "direct_only": True},
            {("R2", "J"), ("R3", "J")},
            id="direct-only-filter",
        ),
        pytest.param(
            {"origins": ["SFO"], "dests": ["NRT"], "cabin": "Y"},
            {("R1", "Y")},
            id="combined-filters",
        ),
    ],
)
def test_query_availability_filters(seeded: Store, kwargs: dict, expected: set) -> None:
    assert _ids(seeded.query_availability(**kwargs)) == expected


def test_query_availability_projects_typed_cabin_fields(seeded: Store) -> None:
    (row,) = seeded.query_availability(origins=["SFO"], dests=["NRT"], cabin="J")
    assert row["mileage_cost"] == 80000
    assert row["available"] is True
    assert row["direct"] is False
    assert row["airlines"] == "NH, UA"
    assert row["raw"]["ID"] == "R1"


def test_query_availability_fresh_within_excludes_stale(db_path: Path) -> None:
    stale_moment = FROZEN - dt.timedelta(hours=30)
    store.connect(db_path, now=_clock(stale_moment)).ingest([ROWS[0]])
    fresh_store = store.connect(db_path, now=_clock(FROZEN))
    fresh_store.ingest([ROWS[1]])
    within_day = fresh_store.query_availability(fresh_within=dt.timedelta(hours=24))
    assert {row["id"] for row in within_day} == {"R2"}
    all_rows = fresh_store.query_availability()
    assert {row["id"] for row in all_rows} == {"R1", "R2"}


def _sweep(label: str, started: dt.datetime, slug: str = "trip") -> dict:
    return {
        "trip_slug": slug,
        "label": label,
        "kind": "search",
        "params": {},
        "started_at": started.isoformat(),
    }


def test_query_trip_scoped_uses_latest_sweep_per_label(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    # first sweep for 'asia' captured all three routes
    st.ingest(ROWS, sweep=_sweep("asia", FROZEN))
    # a refreshed sweep for the same label found only R1 still available
    st.ingest([ROWS[0]], sweep=_sweep("asia", FROZEN + dt.timedelta(hours=1)))
    rows = st.query_availability(trip_slug="trip", labels=["asia"], cabin="Y")
    assert {row["id"] for row in rows} == {"R1"}


def test_query_trip_scoped_latest_sweep_isolated_per_label(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.ingest([ROWS[0], ROWS[1]], sweep=_sweep("asia", FROZEN))
    st.ingest([ROWS[0]], sweep=_sweep("asia", FROZEN + dt.timedelta(hours=1)))
    st.ingest([ROWS[2]], sweep=_sweep("europe", FROZEN))
    rows = st.query_availability(trip_slug="trip", cabin="Y")
    assert {row["id"] for row in rows} == {"R1", "R3"}


def test_trip_detail_roundtrip_and_freshness(db_path: Path) -> None:
    normalized = {"id": "T1", "mileage": 44000, "segments": []}
    st = store.connect(db_path, now=_clock(FROZEN))
    st.trip_detail_put("T1", normalized)
    assert st.trip_detail_get("T1") == normalized
    assert st.trip_detail_get("T1", fresh_within=dt.timedelta(hours=6)) == normalized
    stale = store.connect(db_path, now=_clock(FROZEN + dt.timedelta(hours=7)))
    assert stale.trip_detail_get("T1", fresh_within=dt.timedelta(hours=6)) is None
    assert stale.trip_detail_get("missing") is None


def test_quota_latest_and_events(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", 998)
    st.record_quota("/trips", 995)
    assert st.latest_quota() == {
        "endpoint": "/trips",
        "remaining": 995,
        "recorded_at": FROZEN.isoformat(),
        "reset": False,
    }
    assert [event["remaining"] for event in st.quota_events()] == [995, 998]


def test_latest_quota_orders_by_recorded_at_not_insertion(db_path: Path) -> None:
    # a slower response is inserted later (higher rowid) but its rate-limit
    # snapshot predates the faster one, so it must not win as "latest".
    late = store.connect(db_path, now=_clock(FROZEN + dt.timedelta(seconds=10)))
    late.record_quota("/search", 40)
    early = store.connect(db_path, now=_clock(FROZEN))
    early.record_quota("/search", 90)
    assert late.latest_quota()["remaining"] == 40
    assert late.latest_quota()["recorded_at"] == (FROZEN + dt.timedelta(seconds=10)).isoformat()


def test_latest_quota_tiebreak_prefers_lower_remaining(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", 40)
    st.record_quota("/search", 90)  # same recorded_at, inserted later
    assert st.latest_quota()["remaining"] == 40


def test_latest_quota_flags_previous_utc_day_as_reset(db_path: Path) -> None:
    yesterday = FROZEN - dt.timedelta(days=1)
    store.connect(db_path, now=_clock(yesterday)).record_quota("/search", 10)
    today = store.connect(db_path, now=_clock(FROZEN))
    assert today.latest_quota()["reset"] is True
    today.record_quota("/search", 8)
    assert today.latest_quota()["reset"] is False


def test_latest_quota_raises_no_data_when_empty(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    with pytest.raises(NoData):
        st.latest_quota()


def test_prune_drops_stale_rows_and_cascades(db_path: Path) -> None:
    old = store.connect(db_path, now=_clock(FROZEN - dt.timedelta(days=3)))
    old.ingest([ROWS[0]])
    recent = store.connect(db_path, now=_clock(FROZEN))
    recent.ingest([ROWS[1]])
    result = recent.prune(dt.timedelta(days=2))
    assert result["availability"] == 1
    assert recent.stats()["availability"] == 1
    assert recent.stats()["availability_cabin"] == 4
    assert {row["id"] for row in recent.query_availability()} == {"R2"}


def test_cache_stats_command_reports_counts(getaway_home: Path) -> None:
    store.connect(getaway_home / "cache.db", now=_clock(FROZEN)).ingest(ROWS)
    result = CliRunner().invoke(store.cache_group, ["stats"])
    assert result.exit_code == EXIT_OK
    assert json.loads(result.output)["availability"] == 3


def test_cache_prune_command_reports_removed(getaway_home: Path) -> None:
    ancient = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    store.connect(getaway_home / "cache.db", now=_clock(ancient)).ingest(ROWS)
    result = CliRunner().invoke(store.cache_group, ["prune", "--older-than", "1h"])
    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout) == {"availability": 3, "trip_details": 0}


def test_cache_prune_warns_about_checkpoint_staleness(getaway_home: Path) -> None:
    ancient = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    store.connect(getaway_home / "cache.db", now=_clock(ancient)).ingest(ROWS)
    result = CliRunner().invoke(store.cache_group, ["prune", "--older-than", "1h"])
    assert result.exit_code == EXIT_OK
    assert "checkpoint" in result.stderr.lower()
    assert "refresh" in result.stderr.lower()


def test_cache_prune_silent_when_nothing_removed(getaway_home: Path) -> None:
    store.connect(getaway_home / "cache.db", now=_clock(FROZEN)).ingest(ROWS)
    result = CliRunner().invoke(store.cache_group, ["prune", "--older-than", "999d"])
    assert result.exit_code == EXIT_OK
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"availability": 0, "trip_details": 0}


def test_quota_command_prints_latest(getaway_home: Path) -> None:
    st = store.connect(getaway_home / "cache.db", now=_clock(FROZEN))
    st.record_quota("/search", 812)
    result = CliRunner().invoke(store.quota_cmd, [])
    assert result.exit_code == EXIT_OK
    assert json.loads(result.output)["remaining"] == 812


def test_quota_command_without_events_exits_no_data(getaway_home: Path) -> None:
    store.connect(getaway_home / "cache.db", now=_clock(FROZEN))
    result = CliRunner().invoke(store.quota_cmd, [])
    assert result.exit_code == EXIT_NO_DATA


@pytest.mark.parametrize(
    ("remaining", "floor", "exit_code"),
    [
        pytest.param(500, 100, EXIT_OK, id="above-floor-ok"),
        pytest.param(80, 100, EXIT_NEGATIVE, id="below-floor-negative"),
    ],
)
def test_quota_check_floor_gate(
    getaway_home: Path, remaining: int, floor: int, exit_code: int
) -> None:
    # record on the current UTC day so the reset path stays out of the way; the
    # command reads with the real clock, so a frozen past date would read as reset.
    st = store.connect(getaway_home / "cache.db")
    st.record_quota("/search", remaining)
    result = CliRunner().invoke(store.quota_cmd, ["check", "--floor", str(floor)])
    assert result.exit_code == exit_code


def test_quota_check_previous_utc_day_reports_reset(getaway_home: Path) -> None:
    ancient = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    st = store.connect(getaway_home / "cache.db", now=_clock(ancient))
    st.record_quota("/search", 3)  # far below any floor, but from a prior UTC day
    result = CliRunner().invoke(store.quota_cmd, ["check", "--floor", "100"])
    assert result.exit_code == EXIT_OK
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["reset"] is True
    assert "reset" in result.stderr.lower()


def test_quota_command_reports_reset_flag(getaway_home: Path) -> None:
    ancient = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    store.connect(getaway_home / "cache.db", now=_clock(ancient)).record_quota("/search", 3)
    result = CliRunner().invoke(store.quota_cmd, [])
    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)["reset"] is True


def test_quota_check_without_events_exits_no_data(getaway_home: Path) -> None:
    store.connect(getaway_home / "cache.db", now=_clock(FROZEN))
    result = CliRunner().invoke(store.quota_cmd, ["check", "--floor", "100"])
    assert result.exit_code == EXIT_NO_DATA


def _reservations(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM quota_reservations").fetchone()[0]
    finally:
        conn.close()


def test_reserve_bootstrap_admits_lone_caller_regardless_of_floor(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    # No recorded remaining today: a lone first caller proceeds to learn the
    # header whatever the floor, tracked as in flight.
    st.reserve_quota(999)
    assert _reservations(db_path) == 1


def test_reserve_serializes_concurrent_bootstrap(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    # Two first-ever callers under an unknown quota must not both admit: the
    # second sees the bootstrap in flight and is refused, not double-spent.
    st.reserve_quota(100)
    with pytest.raises(QuotaFloorError) as excinfo:
        st.reserve_quota(100)
    assert "in flight" in str(excinfo.value)
    assert _reservations(db_path) == 1  # the refused bootstrap left no row


def test_reserve_governed_by_floor_after_bootstrap_reconciles(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    token = st.reserve_quota(100)  # bootstrap admits under an unknown quota
    st.reconcile_quota(token, "/search", 500)  # header teaches remaining=500
    st.reserve_quota(100)  # now the learned floor governs, not the bootstrap rule
    assert _reservations(db_path) == 1
    assert st.latest_quota()["remaining"] == 500


def test_reserve_refuses_the_call_that_would_cross_the_floor(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", 101)
    st.reserve_quota(100)  # 101 - 1 == 100, still at the floor
    with pytest.raises(QuotaFloorError) as excinfo:
        st.reserve_quota(100)  # 101 - 2 == 99, below the floor
    assert "floor 100" in str(excinfo.value)
    assert _reservations(db_path) == 1  # the refused reservation left no row


def test_reserve_counts_reservations_against_the_floor(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", 103)
    for _ in range(3):
        st.reserve_quota(100)  # 103 down to exactly 100
    with pytest.raises(QuotaFloorError):
        st.reserve_quota(100)
    assert _reservations(db_path) == 3


@pytest.mark.parametrize(
    ("remaining", "floor", "allowed"),
    [
        pytest.param(50, 0, True, id="floor-zero-spends-below-default"),
        pytest.param(1, 0, True, id="floor-zero-spends-last-unit"),
        pytest.param(50, 100, False, id="default-floor-blocks-below"),
    ],
)
def test_reserve_floor_policy(db_path: Path, remaining: int, floor: int, allowed: bool) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", remaining)
    if allowed:
        st.reserve_quota(floor)
        assert _reservations(db_path) == 1
    else:
        with pytest.raises(QuotaFloorError):
            st.reserve_quota(floor)
        assert _reservations(db_path) == 0


def test_reconcile_releases_reservation_and_records_header(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    token = st.reserve_quota(100)
    st.reconcile_quota(token, "/search", 500)
    assert _reservations(db_path) == 0
    assert st.latest_quota()["remaining"] == 500


def test_reconcile_without_header_releases_without_recording(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    token = st.reserve_quota(100)
    st.reconcile_quota(token, "/routes", None)
    assert _reservations(db_path) == 0
    assert st.quota_events() == []


def test_out_of_order_header_cannot_restore_quota(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", 800)
    st.record_quota("/search", 900)  # a slower, staler response arriving later
    assert st.latest_quota()["remaining"] == 800  # the day minimum, never restored


def test_reserve_reads_conservative_remaining_under_out_of_order(db_path: Path) -> None:
    st = store.connect(db_path, now=_clock(FROZEN))
    st.record_quota("/search", 101)
    st.record_quota("/search", 200)  # out-of-order high value must not lift the floor check
    with pytest.raises(QuotaFloorError):
        st.reserve_quota(101)  # min(101, 200) - 1 == 100 < 101


def test_reserve_prunes_abandoned_reservations(db_path: Path) -> None:
    stale_moment = FROZEN - store.QUOTA_RESERVATION_TTL - dt.timedelta(seconds=1)
    dead = store.connect(db_path, now=_clock(stale_moment))
    dead.record_quota("/search", 101)
    dead.reserve_quota(100)  # a reservation the crashed process never released
    live = store.connect(db_path, now=_clock(FROZEN))
    live.reserve_quota(100)  # allowed only because the abandoned one is pruned
    assert _reservations(db_path) == 1


def test_concurrent_processes_cannot_jointly_cross_the_floor(db_path: Path) -> None:
    store.connect(db_path).record_quota("/search", 103)  # real clock: 3 calls allowed above 100
    workers = 8
    env = os.environ.copy()

    def reserve(_: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, RUNNER, "reserve", str(db_path), "100"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(reserve, range(workers)))
    reserved = [r for r in results if r.returncode == EXIT_OK]
    refused = [r for r in results if r.returncode == EXIT_NEGATIVE]
    assert len(reserved) + len(refused) == workers, [r.stderr for r in results]
    assert len(reserved) == 3  # exactly remaining - floor calls land; the rest are refused
    assert _reservations(db_path) == 3


def test_concurrent_bootstrap_admits_exactly_one(db_path: Path) -> None:
    store.connect(db_path)  # schema only: no recorded quota, so every caller bootstraps
    workers = 8
    env = os.environ.copy()

    def reserve(_: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, RUNNER, "reserve", str(db_path), "100"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(reserve, range(workers)))
    reserved = [r for r in results if r.returncode == EXIT_OK]
    refused = [r for r in results if r.returncode == EXIT_NEGATIVE]
    assert len(reserved) + len(refused) == workers, [r.stderr for r in results]
    assert len(reserved) == 1  # only the first bootstrap admits; the rest are refused
    assert _reservations(db_path) == 1
