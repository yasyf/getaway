import json
from pathlib import Path

import pytest

from getaway import paths


def test_atomic_update_creates_file_on_first_use(getaway_home: Path) -> None:
    target = paths.prefs_path()
    assert not target.exists()
    result = paths.atomic_update(target, lambda d: {**d, "origin": "SFO"})
    assert result == {"origin": "SFO"}
    assert target.exists()
    assert json.loads(target.read_text()) == {"origin": "SFO"}


@pytest.mark.parametrize(
    ("initial", "mutate", "expected"),
    [
        pytest.param(
            {"origin": "SFO"},
            lambda d: {**d, "party": 2},
            {"origin": "SFO", "party": 2},
            id="merges-new-key",
        ),
        pytest.param(
            {"party": 1},
            lambda d: {**d, "party": 4},
            {"party": 4},
            id="overwrites-existing-key",
        ),
    ],
)
def test_atomic_update_merges_into_existing(
    getaway_home: Path, initial: dict, mutate: paths.Mutator, expected: dict
) -> None:
    target = paths.prefs_path()
    paths.atomic_write_text(target, json.dumps(initial))
    result = paths.atomic_update(target, mutate)
    assert result == expected
    assert json.loads(target.read_text()) == expected


def test_atomic_update_raises_on_preexisting_invalid_json(getaway_home: Path) -> None:
    target = paths.prefs_path()
    paths.atomic_write_text(target, "{not valid json")
    with pytest.raises(json.JSONDecodeError):
        paths.atomic_update(target, lambda d: d)


def test_atomic_write_text_roundtrip(getaway_home: Path) -> None:
    target = paths.current_pointer()
    paths.atomic_write_text(target, "2026-07-warm-beachy-week")
    assert target.read_text() == "2026-07-warm-beachy-week"


def test_atomic_update_locks_persistent_sidecar_not_target(getaway_home: Path) -> None:
    target = paths.prefs_path()
    lock = target.with_name(target.name + ".lock")
    paths.atomic_update(target, lambda d: {**d, "n": 1})
    assert lock.exists()
    lock_inode = lock.stat().st_ino
    first_target_inode = target.stat().st_ino
    paths.atomic_update(target, lambda d: {**d, "n": d["n"] + 1})
    # os.replace swaps the target inode on every write; the sidecar lock inode
    # stays stable because it is never replaced or deleted — that is the fix.
    assert target.stat().st_ino != first_target_inode
    assert lock.stat().st_ino == lock_inode
    assert json.loads(target.read_text()) == {"n": 2}


def test_atomic_write_text_locks_persistent_sidecar(getaway_home: Path) -> None:
    target = paths.current_pointer()
    lock = target.with_name(target.name + ".lock")
    paths.atomic_write_text(target, "slug-a")
    lock_inode = lock.stat().st_ino
    paths.atomic_write_text(target, "slug-b")
    assert lock.exists()
    assert lock.stat().st_ino == lock_inode
    assert target.read_text() == "slug-b"


def test_getaway_home_env_override(getaway_home: Path) -> None:
    assert paths.getaway_home() == getaway_home
    assert paths.prefs_path() == getaway_home / "preferences.json"


def test_getaway_home_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GETAWAY_HOME", raising=False)
    assert paths.getaway_home() == Path.home() / ".getaway"
