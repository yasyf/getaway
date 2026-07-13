import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest

FROZEN_NOW = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def getaway_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "getaway"
    monkeypatch.setenv("GETAWAY_HOME", str(home))
    return home


@pytest.fixture
def frozen_clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN_NOW
