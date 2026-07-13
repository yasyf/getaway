import datetime as dt
import json
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from getaway import learnings
from getaway.paths import UsageError, learnings_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_add_appends_row(getaway_home: Path, frozen_clock: Callable[[], dt.datetime]) -> None:
    row = learnings.add("rate limit is 100/min", "api", now=frozen_clock)
    assert row == {
        "ts": "2026-07-13T12:00:00+00:00",
        "text": "rate limit is 100/min",
        "scope": "api",
    }
    assert learnings.list_() == [row]


def test_add_rejects_unknown_scope(getaway_home: Path) -> None:
    with pytest.raises(UsageError):
        learnings.add("nope", "planning")


def test_list_newest_last_and_scope_filter(
    getaway_home: Path, frozen_clock: Callable[[], dt.datetime]
) -> None:
    learnings.add("first", "api", now=frozen_clock)
    learnings.add("second", "prefs", now=frozen_clock)
    learnings.add("third", "api", now=frozen_clock)
    assert [r["text"] for r in learnings.list_()] == ["first", "second", "third"]
    assert [r["text"] for r in learnings.list_(scope="api")] == ["first", "third"]
    assert [r["text"] for r in learnings.list_(n=2)] == ["second", "third"]


def test_list_empty_when_no_file(getaway_home: Path) -> None:
    assert learnings.list_() == []


def test_add_creates_file_0600(getaway_home: Path) -> None:
    learnings.add("rate limit is 100/min", "api")
    assert stat.S_IMODE(learnings_path().stat().st_mode) == 0o600


def test_cli_add_and_list(getaway_home: Path, runner: CliRunner) -> None:
    add = runner.invoke(
        learnings.learnings_group, ["add", "cache TTL is 24h", "--scope", "general"]
    )
    assert add.exit_code == 0
    assert json.loads(add.stdout)["scope"] == "general"
    listed = runner.invoke(learnings.learnings_group, ["list", "--scope", "general"])
    assert [r["text"] for r in json.loads(listed.stdout)] == ["cache TTL is 24h"]


def test_cli_add_bad_scope_exits_usage(getaway_home: Path, runner: CliRunner) -> None:
    result = runner.invoke(learnings.learnings_group, ["add", "x", "--scope", "bogus"])
    assert result.exit_code == 64
