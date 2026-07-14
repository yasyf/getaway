from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import click

from getaway import paths
from getaway.constants import EXIT_NEGATIVE, EXIT_NO_DATA

APPLICATION_ID = 0x47544157  # 'GTAW'
USER_VERSION = 2
BUSY_TIMEOUT_MS = 5000
# A reservation older than this is an abandoned in-flight call (the process died
# mid-request); reserve prunes it so a crash can't lock quota below the floor forever.
QUOTA_RESERVATION_TTL = dt.timedelta(minutes=5)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS availability (
    id TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    dest TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    origin_region TEXT,
    dest_region TEXT,
    updated_at TEXT,
    fetched_at TEXT NOT NULL,
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS availability_route ON availability(origin, dest, date);
CREATE INDEX IF NOT EXISTS availability_source_date ON availability(source, date);

CREATE TABLE IF NOT EXISTS availability_cabin (
    id TEXT NOT NULL,
    cabin TEXT NOT NULL,
    available INTEGER NOT NULL,
    mileage_cost INTEGER NOT NULL,
    remaining_seats INTEGER NOT NULL,
    airlines TEXT NOT NULL,
    direct INTEGER NOT NULL,
    PRIMARY KEY (id, cabin),
    FOREIGN KEY (id) REFERENCES availability(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_slug TEXT,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL,
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sweep_rows (
    sweep_id INTEGER NOT NULL,
    availability_id TEXT NOT NULL,
    PRIMARY KEY (sweep_id, availability_id),
    FOREIGN KEY (sweep_id) REFERENCES sweeps(id) ON DELETE CASCADE,
    FOREIGN KEY (availability_id) REFERENCES availability(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS trip_details (
    id TEXT PRIMARY KEY,
    normalized TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    remaining INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_reservations (
    token TEXT PRIMARY KEY,
    reserved_at TEXT NOT NULL
);
"""

_DURATION_PATTERN = re.compile(r"^(\d+)([hd])$")

Clock = Callable[[], dt.datetime]
Row = dict[str, Any]
Sweep = dict[str, Any]


class NoData(Exception):
    """A cache lookup found no rows where at least one was required."""


class QuotaFloorError(Exception):
    """Reserving a call would draw the seats.aero allowance below the floor."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_duration(value: str) -> dt.timedelta:
    match = _DURATION_PATTERN.match(value)
    if match is None:
        raise click.BadParameter(f"invalid duration {value!r}: use forms like 6h or 7d")
    quantity = int(match.group(1))
    unit = match.group(2)
    return dt.timedelta(hours=quantity) if unit == "h" else dt.timedelta(days=quantity)


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version={USER_VERSION}")
    conn.commit()


def _delete_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def connect(path: Path, now: Clock = _utcnow) -> Store:
    with paths.locked(path):
        conn = _open(path)
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if app_id == 0:
            _create_schema(conn)
        elif app_id == APPLICATION_ID and version == USER_VERSION:
            pass
        else:
            conn.close()
            _delete_db(path)
            conn = _open(path)
            _create_schema(conn)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                sidecar.chmod(0o600)
    return Store(conn, path, now=now)


class Store:
    def __init__(self, conn: sqlite3.Connection, path: Path, now: Clock = _utcnow) -> None:
        self._conn = conn
        self._path = path
        self._now = now

    def ingest(self, rows: Sequence[Row], sweep: Sweep | None = None) -> dict[str, Any]:
        # function-level import: seats owns cabin normalization at the client boundary
        # and imports connect() from this module, so the reference is cycled lazily.
        from getaway.seats import cabin_rows

        fetched_at = self._now().isoformat()
        sweep_id: int | None = None
        superseded: list[Row] = []
        if sweep is not None:
            self._conn.execute("BEGIN IMMEDIATE")
            prior_rows = self._conn.execute(
                "SELECT a.id, a.date FROM availability a "
                "JOIN sweep_rows sr ON sr.availability_id = a.id "
                "WHERE sr.sweep_id = ("
                "SELECT MAX(s.id) FROM sweeps s WHERE s.trip_slug = ? AND s.label = ?) "
                "ORDER BY a.id",
                (sweep["trip_slug"], sweep["label"]),
            ).fetchall()
            row_ids = {row["ID"] for row in rows}
            superseded = [
                {"id": row["id"], "date": row["date"]}
                for row in prior_rows
                if row["id"] not in row_ids
            ]
            cursor = self._conn.execute(
                "INSERT INTO sweeps (trip_slug, label, kind, params, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    sweep["trip_slug"],
                    sweep["label"],
                    sweep["kind"],
                    json.dumps(sweep["params"]),
                    sweep["started_at"],
                ),
            )
            sweep_id = cursor.lastrowid
        new = 0
        for row in rows:
            row_id = row["ID"]
            route = row["Route"]
            existed = self._conn.execute(
                "SELECT 1 FROM availability WHERE id = ?", (row_id,)
            ).fetchone()
            if existed is None:
                new += 1
            self._conn.execute(
                "INSERT INTO availability "
                "(id, origin, dest, date, source, origin_region, dest_region, "
                " updated_at, fetched_at, raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                " origin=excluded.origin, dest=excluded.dest, date=excluded.date, "
                " source=excluded.source, origin_region=excluded.origin_region, "
                " dest_region=excluded.dest_region, updated_at=excluded.updated_at, "
                " fetched_at=excluded.fetched_at, raw=excluded.raw",
                (
                    row_id,
                    route["OriginAirport"],
                    route["DestinationAirport"],
                    row["Date"],
                    row["Source"],
                    route.get("OriginRegion"),
                    route.get("DestinationRegion"),
                    row.get("UpdatedAt"),
                    fetched_at,
                    json.dumps(row),
                ),
            )
            for cabin in cabin_rows(row):
                self._conn.execute(
                    "INSERT INTO availability_cabin "
                    "(id, cabin, available, mileage_cost, remaining_seats, airlines, direct) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id, cabin) DO UPDATE SET "
                    " available=excluded.available, mileage_cost=excluded.mileage_cost, "
                    " remaining_seats=excluded.remaining_seats, airlines=excluded.airlines, "
                    " direct=excluded.direct",
                    (
                        row_id,
                        cabin["cabin"],
                        int(cabin["available"]),
                        cabin["mileage_cost"],
                        cabin["remaining_seats"],
                        cabin["airlines"],
                        int(cabin["direct"]),
                    ),
                )
            if sweep_id is not None:
                self._conn.execute(
                    "INSERT OR IGNORE INTO sweep_rows (sweep_id, availability_id) VALUES (?, ?)",
                    (sweep_id, row_id),
                )
        self._conn.commit()
        return {"rows": len(rows), "new": new, "superseded": superseded}

    def query_availability(
        self,
        origins: Iterable[str] | None = None,
        dests: Iterable[str] | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        cabin: str | None = None,
        min_seats: int | None = None,
        max_mileage: int | None = None,
        sources: Iterable[str] | None = None,
        fresh_within: dt.timedelta | None = None,
        direct_only: bool = False,
        trip_slug: str | None = None,
        labels: Iterable[str] | None = None,
        kinds: Iterable[str] | None = None,
    ) -> list[Row]:
        clauses: list[str] = []
        params: list[Any] = []
        origins = list(origins) if origins is not None else None
        dests = list(dests) if dests is not None else None
        sources = list(sources) if sources is not None else None
        labels = list(labels) if labels is not None else None
        kinds = list(kinds) if kinds is not None else None
        if trip_slug is not None:
            placeholders = ",".join("?" for _ in labels) if labels else ""
            label_clause = f"AND s.label IN ({placeholders})" if labels else ""
            kind_placeholders = ",".join("?" for _ in kinds) if kinds else ""
            kind_clause = f"AND s.kind IN ({kind_placeholders})" if kinds else ""
            # Only the latest sweep per (trip_slug, label) counts: a refreshed
            # sweep's smaller result set must supersede the prior one, not union.
            clauses.append(
                "a.id IN (SELECT sr.availability_id FROM sweep_rows sr "
                "WHERE sr.sweep_id IN ("
                "SELECT MAX(s.id) FROM sweeps s "
                f"WHERE s.trip_slug = ? {label_clause} {kind_clause} GROUP BY s.label))"
            )
            params.append(trip_slug)
            if labels:
                params.extend(labels)
            if kinds:
                params.extend(kinds)
        if origins:
            clauses.append(f"a.origin IN ({','.join('?' for _ in origins)})")
            params.extend(origins)
        if dests:
            clauses.append(f"a.dest IN ({','.join('?' for _ in dests)})")
            params.extend(dests)
        if date_start is not None:
            clauses.append("a.date >= ?")
            params.append(date_start)
        if date_end is not None:
            clauses.append("a.date <= ?")
            params.append(date_end)
        if sources:
            clauses.append(f"a.source IN ({','.join('?' for _ in sources)})")
            params.extend(sources)
        if fresh_within is not None:
            clauses.append("a.fetched_at >= ?")
            params.append((self._now() - fresh_within).isoformat())
        if cabin is not None:
            clauses.append("c.cabin = ?")
            params.append(cabin)
        if min_seats is not None:
            clauses.append("c.remaining_seats >= ?")
            params.append(min_seats)
        if max_mileage is not None:
            clauses.append("c.mileage_cost <= ?")
            params.append(max_mileage)
        if direct_only:
            clauses.append("c.direct = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT a.id AS id, a.origin, a.dest, a.date, a.source, "
            " a.origin_region, a.dest_region, a.updated_at, a.fetched_at, a.raw, "
            " c.cabin, c.available, c.mileage_cost, c.remaining_seats, c.airlines, c.direct "
            "FROM availability a JOIN availability_cabin c ON a.id = c.id "
            f"{where} "
            "ORDER BY a.date, a.id, c.cabin"
        )
        return [_project(row) for row in self._conn.execute(sql, params).fetchall()]

    def trip_detail_get(
        self, availability_id: str, fresh_within: dt.timedelta | None = None
    ) -> Row | None:
        row = self._conn.execute(
            "SELECT normalized, fetched_at FROM trip_details WHERE id = ?", (availability_id,)
        ).fetchone()
        if row is None:
            return None
        if fresh_within is not None:
            fetched = dt.datetime.fromisoformat(row["fetched_at"])
            if fetched < self._now() - fresh_within:
                return None
        return json.loads(row["normalized"])

    def trip_detail_put(self, availability_id: str, normalized: Row) -> None:
        self._conn.execute(
            "INSERT INTO trip_details (id, normalized, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            " normalized=excluded.normalized, fetched_at=excluded.fetched_at",
            (availability_id, json.dumps(normalized), self._now().isoformat()),
        )
        self._conn.commit()

    def record_quota(self, endpoint: str, remaining: int) -> None:
        self._conn.execute(
            "INSERT INTO quota_events (endpoint, remaining, recorded_at) VALUES (?, ?, ?)",
            (endpoint, remaining, self._now().isoformat()),
        )
        self._conn.commit()

    def reserve_quota(self, floor: int) -> str:
        """Claim one call's worth of quota, refusing if it would cross the floor.

        Runs a short read-modify-write under the store lock — never spanning the
        HTTP call — so two processes cannot jointly draw the allowance below
        ``floor``. The reservation counts against every other in-flight caller
        until ``reconcile_quota`` releases it. When today's allowance is not yet
        known (a fresh day, or the first call ever), a lone caller is admitted so
        the response header can teach it, but a concurrent second caller is
        refused until that bootstrap reconciles — two callers admitting under an
        unknown quota could otherwise jointly overshoot the floor. Raises
        ``QuotaFloorError`` otherwise.
        """
        token = uuid.uuid4().hex
        with paths.locked(self._path), self._conn:
            self._prune_stale_reservations()
            remaining = self._today_remaining()
            in_flight = self._conn.execute(
                "SELECT COUNT(*) FROM quota_reservations"
            ).fetchone()[0]
            if remaining is None:
                if in_flight:
                    raise QuotaFloorError(
                        "seats.aero quota unknown; a bootstrap call is in flight"
                    )
            elif remaining - (in_flight + 1) < floor:
                raise QuotaFloorError(
                    f"seats.aero quota floor {floor} reached: {remaining} remaining, "
                    f"{in_flight} in flight"
                )
            self._conn.execute(
                "INSERT INTO quota_reservations (token, reserved_at) VALUES (?, ?)",
                (token, self._now().isoformat()),
            )
        return token

    def reconcile_quota(self, token: str, endpoint: str, remaining: int | None) -> None:
        """Release a reservation and fold in the response's quota header.

        A second short critical section, disjoint from the HTTP call. Recording a
        header is monotonic by construction: ``latest_quota`` and ``reserve_quota``
        read the day's *minimum* recorded remaining, so an out-of-order response
        carrying a staler (higher) count can never restore quota.
        """
        with paths.locked(self._path):
            self._conn.execute("DELETE FROM quota_reservations WHERE token = ?", (token,))
            if remaining is not None:
                self._conn.execute(
                    "INSERT INTO quota_events (endpoint, remaining, recorded_at) VALUES (?, ?, ?)",
                    (endpoint, remaining, self._now().isoformat()),
                )
            self._conn.commit()

    def latest_quota(self) -> Row:
        # The allowance only decreases within a UTC day, so the day's minimum
        # recorded remaining is the conservative truth: an out-of-order response
        # with a staler (higher) count can't raise it.
        today = self._conn.execute(
            "SELECT endpoint, remaining, recorded_at FROM quota_events "
            "WHERE recorded_at >= ? ORDER BY remaining ASC, recorded_at DESC LIMIT 1",
            (self._day_start().isoformat(),),
        ).fetchone()
        if today is not None:
            return {
                "endpoint": today["endpoint"],
                "remaining": today["remaining"],
                "recorded_at": today["recorded_at"],
                "reset": False,
            }
        # No event today: the midnight-UTC reset means the last known count no
        # longer reflects the allowance, so flag it and let the next call relearn.
        latest = self._conn.execute(
            "SELECT endpoint, remaining, recorded_at FROM quota_events "
            "ORDER BY recorded_at DESC, remaining ASC LIMIT 1"
        ).fetchone()
        if latest is None:
            raise NoData("no quota events recorded")
        return {
            "endpoint": latest["endpoint"],
            "remaining": latest["remaining"],
            "recorded_at": latest["recorded_at"],
            "reset": True,
        }

    def _today_remaining(self) -> int | None:
        row = self._conn.execute(
            "SELECT MIN(remaining) AS remaining FROM quota_events WHERE recorded_at >= ?",
            (self._day_start().isoformat(),),
        ).fetchone()
        return row["remaining"]

    def _prune_stale_reservations(self) -> None:
        cutoff = (self._now() - QUOTA_RESERVATION_TTL).isoformat()
        self._conn.execute("DELETE FROM quota_reservations WHERE reserved_at < ?", (cutoff,))

    def _day_start(self) -> dt.datetime:
        return self._now().replace(hour=0, minute=0, second=0, microsecond=0)

    def quota_events(self, limit: int | None = None) -> list[Row]:
        sql = "SELECT endpoint, remaining, recorded_at FROM quota_events ORDER BY id DESC"
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [
            {
                "endpoint": row["endpoint"],
                "remaining": row["remaining"],
                "recorded_at": row["recorded_at"],
            }
            for row in self._conn.execute(sql, params).fetchall()
        ]

    def stats(self, trip_slug: str | None = None) -> dict[str, Any]:
        if trip_slug is None:
            return {
                "availability": self._count("availability"),
                "availability_cabin": self._count("availability_cabin"),
                "sweeps": self._count("sweeps"),
                "trip_details": self._count("trip_details"),
                "quota_events": self._count("quota_events"),
            }
        sweeps = self._conn.execute(
            "SELECT COUNT(*) FROM sweeps WHERE trip_slug = ?", (trip_slug,)
        ).fetchone()[0]
        rows = self._conn.execute(
            "SELECT COUNT(DISTINCT sr.availability_id) FROM sweep_rows sr "
            "JOIN sweeps s ON s.id = sr.sweep_id WHERE s.trip_slug = ?",
            (trip_slug,),
        ).fetchone()[0]
        return {"trip_slug": trip_slug, "sweeps": sweeps, "rows": rows}

    def prune(self, older_than: dt.timedelta) -> dict[str, int]:
        cutoff = (self._now() - older_than).isoformat()
        availability = self._conn.execute(
            "DELETE FROM availability WHERE fetched_at < ?", (cutoff,)
        ).rowcount
        trip_details = self._conn.execute(
            "DELETE FROM trip_details WHERE fetched_at < ?", (cutoff,)
        ).rowcount
        self._conn.commit()
        return {"availability": availability, "trip_details": trip_details}

    def _count(self, table: str) -> int:
        return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _project(row: sqlite3.Row) -> Row:
    return {
        "id": row["id"],
        "origin": row["origin"],
        "dest": row["dest"],
        "date": row["date"],
        "source": row["source"],
        "origin_region": row["origin_region"],
        "dest_region": row["dest_region"],
        "updated_at": row["updated_at"],
        "fetched_at": row["fetched_at"],
        "cabin": row["cabin"],
        "available": bool(row["available"]),
        "mileage_cost": row["mileage_cost"],
        "remaining_seats": row["remaining_seats"],
        "airlines": row["airlines"],
        "direct": bool(row["direct"]),
        "raw": json.loads(row["raw"]),
    }


@click.group("cache")
def cache_group() -> None:
    """Ad-hoc queries over the derived availability cache."""


@cache_group.command("query")
@click.option("--origin", "origins", multiple=True)
@click.option("--dest", "dests", multiple=True)
@click.option("--date-start")
@click.option("--date-end")
@click.option("--cabin")
@click.option("--min-seats", type=int)
@click.option("--max-mileage", type=int)
@click.option("--source", "sources", multiple=True)
@click.option("--fresh-within")
@click.option("--direct", "direct_only", is_flag=True)
def cache_query(
    origins: tuple[str, ...],
    dests: tuple[str, ...],
    date_start: str | None,
    date_end: str | None,
    cabin: str | None,
    min_seats: int | None,
    max_mileage: int | None,
    sources: tuple[str, ...],
    fresh_within: str | None,
    direct_only: bool,
) -> None:
    store = connect(paths.cache_db())
    rows = store.query_availability(
        origins=list(origins) or None,
        dests=list(dests) or None,
        date_start=date_start,
        date_end=date_end,
        cabin=cabin,
        min_seats=min_seats,
        max_mileage=max_mileage,
        sources=list(sources) or None,
        fresh_within=parse_duration(fresh_within) if fresh_within else None,
        direct_only=direct_only,
    )
    click.echo(json.dumps({"rows": rows, "count": len(rows)}))


@cache_group.command("stats")
@click.option("--trip")
def cache_stats(trip: str | None) -> None:
    store = connect(paths.cache_db())
    click.echo(json.dumps(store.stats(trip_slug=trip)))


@cache_group.command("prune")
@click.option("--older-than", required=True)
def cache_prune(older_than: str) -> None:
    store = connect(paths.cache_db())
    result = store.prune(parse_duration(older_than))
    if result["availability"] or result["trip_details"]:
        click.echo(
            "warning: trip checkpoints may now report fresh over pruned rows; "
            "re-run the workflow with refresh to rebuild affected phases",
            err=True,
        )
    click.echo(json.dumps(result))


@click.group("quota", invoke_without_command=True)
@click.pass_context
def quota_cmd(ctx: click.Context) -> None:
    """Report seats.aero quota drawn from recorded call headers."""
    if ctx.invoked_subcommand is not None:
        return
    store = connect(paths.cache_db())
    try:
        latest = store.latest_quota()
    except NoData as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_NO_DATA) from err
    click.echo(json.dumps(latest))


@quota_cmd.command("check")
@click.option("--floor", type=int, required=True)
def quota_check(floor: int) -> None:
    store = connect(paths.cache_db())
    try:
        latest = store.latest_quota()
    except NoData as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_NO_DATA) from err
    remaining = latest["remaining"]
    if latest["reset"]:
        click.echo("quota allowance reset at midnight UTC; last event predates today", err=True)
        click.echo(json.dumps({"remaining": remaining, "floor": floor, "ok": True, "reset": True}))
        return
    if remaining < floor:
        click.echo(f"quota remaining {remaining} is below floor {floor}", err=True)
        raise SystemExit(EXIT_NEGATIVE)
    click.echo(json.dumps({"remaining": remaining, "floor": floor, "ok": True}))
