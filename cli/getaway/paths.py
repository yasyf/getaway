import fcntl
import functools
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import click

from getaway.constants import EXIT_NEGATIVE, EXIT_STATE, EXIT_USAGE

Mutator = Callable[[dict], dict]


class GetawayError(Exception):
    exit_code = EXIT_USAGE


class UsageError(GetawayError):
    exit_code = EXIT_USAGE


class StateConflictError(GetawayError):
    exit_code = EXIT_STATE


class NegativePredicate(GetawayError):
    exit_code = EXIT_NEGATIVE


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _atomic_replace(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as tmp:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(_lock_path(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def emit(obj: object) -> None:
    click.echo(json.dumps(obj, separators=(",", ":")))


def map_errors(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return fn(*args, **kwargs)
        except GetawayError as err:
            click.echo(str(err), err=True)
            raise SystemExit(err.exit_code)

    return wrapper


def require_keys(
    mapping: object, required: set[str], label: str, optional: frozenset[str] = frozenset()
) -> dict:
    if not isinstance(mapping, dict):
        raise UsageError(f"{label} must be an object")
    keys = set(mapping)
    missing = required - keys
    extra = keys - required - optional
    if missing or extra:
        raise UsageError(f"{label} keys: missing={sorted(missing)} extra={sorted(extra)}")
    return mapping


def require_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{label} must be a string")
    return value


def require_str_or_none(value: object, label: str) -> None:
    if value is not None and not isinstance(value, str):
        raise UsageError(f"{label} must be a string or null")


def require_str_list(value: object, label: str) -> None:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise UsageError(f"{label} must be a list of strings")


def require_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise UsageError(f"{label} must be an integer")
    return value


def require_int_or_none(value: object, label: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise UsageError(f"{label} must be an integer or null")


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
    with locked(path):
        raw = path.read_text() if path.exists() else ""
        current = json.loads(raw) if raw else {}
        updated = mutate(current)
        _atomic_replace(path, json.dumps(updated, indent=2) + "\n")
        return updated


def atomic_write_text(path: Path, text: str) -> None:
    with locked(path):
        _atomic_replace(path, text)
