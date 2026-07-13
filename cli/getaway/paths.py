import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

Mutator = Callable[[dict], dict]


def _atomic_replace(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path)


def getaway_home() -> Path:
    env = os.environ.get("GETAWAY_HOME")
    return Path(env) if env else Path.home() / ".getaway"


def prefs_path() -> Path:
    return getaway_home() / "preferences.json"


def learnings_path() -> Path:
    return getaway_home() / "learnings.jsonl"


def trips_dir() -> Path:
    return getaway_home() / "trips"


def trip_dir(slug: str) -> Path:
    return trips_dir() / slug


def current_pointer() -> Path:
    return trips_dir() / "current"


def cache_db() -> Path:
    return getaway_home() / "cache.db"


def atomic_update(path: Path, mutate: Mutator) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        raw = path.read_text()
        current = json.loads(raw) if raw else {}
        updated = mutate(current)
        _atomic_replace(path, json.dumps(updated, indent=2) + "\n")
        return updated
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _atomic_replace(path, text)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
