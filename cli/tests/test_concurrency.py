import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from getaway import prefs

RUNNER = str(Path(__file__).parent / "_runner.py")

DISTINCT_PATCHES = [
    ('{"op_ref": "op://vault/item"}', "op_ref", "op://vault/item"),
    ('{"home_airport": "SFO"}', "home_airport", "SFO"),
    ('{"origin_airports": ["SFO", "OAK"]}', "origin_airports", ["SFO", "OAK"]),
    ('{"avoid_transit": ["LHR"]}', "avoid_transit", ["LHR"]),
    ('{"avoid_destinations": ["ICN"]}', "avoid_destinations", ["ICN"]),
    ('{"departure_days": ["Fri"]}', "departure_days", ["Fri"]),
    (
        '{"avoid_airlines": [{"code": "UA", "name": "United", "strength": "soft"}]}',
        "avoid_airlines",
        [{"code": "UA", "name": "United", "strength": "soft"}],
    ),
    (
        '{"status_goals": [{"program": "united", "target": "1K", "by": "2026-12-31"}]}',
        "status_goals",
        [{"program": "united", "target": "1K", "by": "2026-12-31"}],
    ),
]


def _run(
    args: list[str], env: dict[str, str], stdin: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, RUNNER, *args],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_eight_concurrent_distinct_key_set_patches_all_land(getaway_home: Path) -> None:
    prefs.init()
    env = os.environ.copy()
    with ThreadPoolExecutor(max_workers=len(DISTINCT_PATCHES)) as pool:
        results = list(
            pool.map(lambda patch: _run(["set-patch"], env, stdin=patch[0]), DISTINCT_PATCHES)
        )
    for result in results:
        assert result.returncode == 0, result.stderr
    doc = prefs.show()
    for _, key, value in DISTINCT_PATCHES:
        assert doc[key] == value, key


def test_concurrent_increments_never_lose_updates(getaway_home: Path) -> None:
    counter = getaway_home / "counter.json"
    env = os.environ.copy()
    processes = 3
    iterations = 20
    with ThreadPoolExecutor(max_workers=processes) as pool:
        results = list(
            pool.map(
                lambda _: _run(["incr", str(counter), str(iterations)], env),
                range(processes),
            )
        )
    for result in results:
        assert result.returncode == 0, result.stderr
    assert json.loads(counter.read_text()) == {"n": processes * iterations}
