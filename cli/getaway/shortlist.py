import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import prefs, trips
from getaway.constants import (
    CABIN_PREFIX,
    CASH_CUTOFF_MINUTES,
    EXPANSION_BUFFER_CAP,
    EXPANSION_BUFFER_FACTOR,
)
from getaway.paths import cache_db, emit, map_errors, utcnow
from getaway.store import connect

DAY_TOKENS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

Row = dict[str, Any]


def _weekday_token(date_str: str) -> str:
    return DAY_TOKENS[dt.date.fromisoformat(date_str).weekday()]


def _airlines(row: Row) -> list[str]:
    raw = row["airlines"]
    return [a for a in raw.split(", ") if a]


def _sweep_artifact(label: str) -> str:
    return f"sweep-{label}.jsonl"


def _direct_labels(trip: dict, prefs_doc: dict) -> list[str]:
    from getaway.sweeps import derive_specs

    return [spec["label"] for spec in derive_specs(trip, prefs_doc) if spec["label"] != "gateways"]


def _max_finalists(plan: dict) -> int:
    return plan.get("max_finalists", 6)


def _buffer(plan: dict) -> int:
    return min(EXPANSION_BUFFER_FACTOR * _max_finalists(plan), EXPANSION_BUFFER_CAP)


def _group_best(candidates: list[Row]) -> list[Row]:
    best: dict[tuple[str, str, str, str], Row] = {}
    for cand in candidates:
        key = (cand["origin"], cand["dest"], cand["date"], cand["source"])
        current = best.get(key)
        if current is None or cand["mileage"] < current["mileage"]:
            best[key] = cand
    return list(best.values())


def shortlist(slug: str, gateway: bool = False, now: Callable[[], dt.datetime] = utcnow) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    cabin_letter = CABIN_PREFIX[trip["cabin"]]
    party = trip["party"]
    labels = ["gateways"] if gateway else _direct_labels(trip, prefs_doc)

    store = connect(cache_db(), now=now)
    rows = (
        store.query_availability(trip_slug=slug, labels=labels, cabin=cabin_letter)
        if labels
        else []
    )
    considered = len(rows)

    hard = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "hard"}
    soft = {a["code"] for a in prefs_doc["avoid_airlines"] if a["strength"] == "soft"}
    ceiling = plan.get("mileage_ceiling")
    sources = set(plan["sources"]) if plan.get("sources") else None
    veto: set[str] = (
        set()
        if gateway
        else set(prefs_doc["avoid_destinations"]) | set(trip["avoid_final_destinations"])
    )
    departure_days = set(prefs_doc["departure_days"])

    candidates: list[Row] = []
    for row in rows:
        if not row["available"]:
            continue
        seats = row["remaining_seats"]
        if seats and seats < party:  # seats == 0 is absent data and passes
            continue
        if ceiling is not None and row["mileage_cost"] > ceiling:
            continue
        if sources is not None and row["source"] not in sources:
            continue
        if not gateway and row["dest"] in veto:
            continue
        airlines = _airlines(row)
        if airlines and all(a in hard for a in airlines):
            continue
        candidates.append(
            {
                "id": row["id"],
                "date": row["date"],
                "origin": row["origin"],
                "dest": row["dest"],
                "source": row["source"],
                "mileage": row["mileage_cost"],
                "seats": seats,
                "airlines": row["airlines"],
                "direct": row["direct"],
                "soft": any(a in soft for a in airlines),
                "departure_day_match": bool(departure_days)
                and _weekday_token(row["date"]) in departure_days,
            }
        )

    grouped = _group_best(candidates)
    grouped.sort(key=lambda c: (int(c["soft"]), c["mileage"], 0 if c["departure_day_match"] else 1))
    kept = grouped[: _buffer(plan)]

    name = "shortlist-gateway.json" if gateway else "shortlist.json"
    key = "shortlist:gateway" if gateway else "shortlist"
    doc = {"candidates": kept, "considered": considered}
    trips.artifact_write(slug, name, json.dumps(doc, separators=(",", ":")))
    deps = trips.existing_artifacts(slug, [_sweep_artifact(label) for label in labels])
    trips.phase_done(slug, key, deps, now=now)
    return doc


def onward_minima(slug: str, now: Callable[[], dt.datetime] = utcnow) -> dict:
    trip = trips.show(slug)
    plan = trip["plan"]
    hybrid = plan["hybrid"]
    party = trip["party"]
    gateway_doc = json.loads(trips.artifact_read(slug, "shortlist-gateway.json"))
    gateways = sorted({c["dest"] for c in gateway_doc["candidates"]})
    onward_dests = hybrid["onward_dests"]
    letter_to_cabin = {letter: name for name, letter in CABIN_PREFIX.items()}

    store = connect(cache_db(), now=now)
    rows = store.query_availability(trip_slug=slug, labels=["onward"])
    minima: dict[tuple[str, str, str], Row] = {}
    for row in rows:
        if not row["available"]:
            continue
        if row["origin"] not in gateways or row["dest"] not in onward_dests:
            continue
        seats = row["remaining_seats"]
        if seats and seats < party:
            continue
        cabin = letter_to_cabin[row["cabin"]]
        key = (row["origin"], row["dest"], cabin)
        current = minima.get(key)
        if current is None or row["mileage_cost"] < current["mileage"]:
            minima[key] = {
                "gateway": row["origin"],
                "onward_dest": row["dest"],
                "cabin": cabin,
                "id": row["id"],
                "date": row["date"],
                "source": row["source"],
                "mileage": row["mileage_cost"],
                "seats": seats,
                "airlines": row["airlines"],
                "direct": row["direct"],
            }

    bridge_pairs = [
        {"gateway": g, "onward_dest": d, "cash_cutoff_minutes": CASH_CUTOFF_MINUTES}
        for g in gateways
        for d in onward_dests
        if d != g
    ]
    doc = {"minima": list(minima.values()), "bridge_pairs": bridge_pairs}
    trips.artifact_write(slug, "onward.json", json.dumps(doc, separators=(",", ":")))
    deps = trips.existing_artifacts(slug, ["sweep-onward.jsonl", "shortlist-gateway.json"])
    trips.phase_done(slug, "onward", deps, now=now)
    return doc


shortlist_group = click.Group("shortlist", help="SQL shortlist over a trip's sweep rows.")


@shortlist_group.command("run")
@click.argument("slug")
@click.option("--gateway", is_flag=True)
@map_errors
def _run_cmd(slug: str, gateway: bool) -> None:
    emit(shortlist(slug, gateway=gateway))


@shortlist_group.command("onward")
@click.argument("slug")
@map_errors
def _onward_cmd(slug: str) -> None:
    emit(onward_minima(slug))
