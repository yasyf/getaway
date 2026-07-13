import json
import os
from collections.abc import Callable
from datetime import datetime

import click

from getaway.paths import (
    UsageError,
    emit,
    learnings_path,
    map_errors,
    utcnow,
)

LEARNING_SCOPES = frozenset({"api", "prefs", "general"})


def add(text: str, scope: str, now: Callable[[], datetime] = utcnow) -> dict:
    if scope not in LEARNING_SCOPES:
        raise UsageError(f"scope must be one of {sorted(LEARNING_SCOPES)}")
    row = {"ts": now().isoformat(), "text": text, "scope": scope}
    path = learnings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
    finally:
        os.close(fd)
    return row


def list_(scope: str | None = None, n: int | None = None) -> list[dict]:
    path = learnings_path()
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if scope is not None:
        rows = [r for r in rows if r["scope"] == scope]
    if n is not None:
        rows = rows[-n:]
    return rows


learnings_group = click.Group("learnings", help="Append-only planning learnings.")


@learnings_group.command("add")
@click.argument("text")
@click.option("--scope", required=True)
@map_errors
def _add_cmd(text: str, scope: str) -> None:
    emit(add(text, scope))


@learnings_group.command("list")
@click.option("--scope", default=None)
@click.option("-n", "n", type=int, default=None)
@map_errors
def _list_cmd(scope: str | None, n: int | None) -> None:
    emit(list_(scope, n))
