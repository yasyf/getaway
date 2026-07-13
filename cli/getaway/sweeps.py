import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import trips
from getaway.paths import (
    UsageError,
    cache_db,
    emit,
    map_errors,
    utcnow,
)
from getaway.seats import AuthError, SeatsClient
from getaway.store import connect

SWEEP_TAKE = 1000

Spec = dict[str, Any]


def _region_slug(region: str) -> str:
    return region.lower().replace(" ", "-")


def _search_params(trip: dict, dests: list[str]) -> dict:
    plan = trip["plan"]
    window = trip["window"]
    params: dict[str, Any] = {
        "origins": plan["origins"],
        "dests": dests,
        "start": window["start"],
        "end": window["end"],
        "cabins": [trip["cabin"]],
    }
    if plan.get("sources"):
        params["sources"] = plan["sources"]
    return params


def derive_specs(trip: dict, prefs_doc: dict) -> list[Spec]:
    plan = trip["plan"]
    if not plan:
        return []
    specs: list[Spec] = []
    for bucket in plan.get("buckets", []):
        specs.append(
            {
                "label": bucket["name"],
                "kind": "search",
                "params": _search_params(trip, bucket["dests"]),
            }
        )
    window = trip["window"]
    for sweep in plan.get("program_sweeps", []):
        params: dict[str, Any] = {
            "source": sweep["source"],
            "cabin": trip["cabin"],
            "start": window["start"],
            "end": window["end"],
        }
        region = sweep.get("dest_region") or sweep.get("origin_region")
        if "dest_region" in sweep:
            params["dest_region"] = sweep["dest_region"]
        if "origin_region" in sweep:
            params["origin_region"] = sweep["origin_region"]
        specs.append(
            {
                "label": f"{sweep['source']}-{_region_slug(region)}",
                "kind": "availability",
                "params": params,
            }
        )
    hybrid = plan.get("hybrid")
    if hybrid:
        specs.append(
            {
                "label": "gateways",
                "kind": "search",
                "params": _search_params(trip, hybrid["gateways"]),
            }
        )
    return specs


def _onward_spec(slug: str, trip: dict) -> Spec:
    plan = trip["plan"]
    hybrid = plan["hybrid"]
    gateway_doc = json.loads(trips.artifact_read(slug, "shortlist-gateway.json"))
    gateways = sorted({c["dest"] for c in gateway_doc["candidates"]})
    window = trip["window"]
    # Onward legs span every cabin — the economy/business economics resolve downstream.
    params = {
        "origins": gateways,
        "dests": hybrid["onward_dests"],
        "start": window["start"],
        "end": window["end"],
    }
    return {"label": "onward", "kind": "search", "params": params}


def _resolve_spec(slug: str, label: str, trip: dict, prefs_doc: dict) -> Spec:
    if label == "onward":
        return _onward_spec(slug, trip)
    for spec in derive_specs(trip, prefs_doc):
        if spec["label"] == label:
            return spec
    raise UsageError(f"no sweep spec for label {label!r}")


def _call(client: SeatsClient, spec: Spec) -> list[dict]:
    p = spec["params"]
    if spec["kind"] == "search":
        return client.search(
            p["origins"],
            p["dests"],
            start=p.get("start"),
            end=p.get("end"),
            cabins=p.get("cabins"),
            sources=p.get("sources"),
            take=SWEEP_TAKE,
        )
    return client.availability(
        p["source"],
        cabin=p.get("cabin"),
        start=p.get("start"),
        end=p.get("end"),
        origin_region=p.get("origin_region"),
        dest_region=p.get("dest_region"),
        take=SWEEP_TAKE,
    )


def _artifact_name(label: str) -> str:
    return f"sweep-{label}.jsonl"


def _line_count(slug: str, name: str) -> int:
    text = trips.artifact_read(slug, name)
    return sum(1 for line in text.splitlines() if line.strip())


def run(
    slug: str, label: str, refresh: bool = False, now: Callable[[], dt.datetime] = utcnow
) -> dict:
    from getaway import prefs

    trip = trips.show(slug)
    prefs_doc = prefs.show()
    spec = _resolve_spec(slug, label, trip, prefs_doc)
    key = f"sweep:{label}"
    name = _artifact_name(label)
    if not refresh:
        fresh, _ = trips.phase_check(slug, key, [name], now=now)
        if fresh:
            return {"label": label, "skipped": True, "rows": _line_count(slug, name)}
    store = connect(cache_db(), now=now)
    client = SeatsClient(store)
    rows = _call(client, spec)
    sweep = {
        "trip_slug": slug,
        "label": label,
        "kind": spec["kind"],
        "params": spec["params"],
        "started_at": now().isoformat(),
    }
    result = store.ingest(rows, sweep=sweep)
    content = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    trips.artifact_write(slug, name, content)
    quota = _quota_remaining(store)
    trips.phase_done(slug, key, [name], quota_after=quota, now=now)
    return {"label": label, "rows": result["rows"], "new": result["new"], "quota_remaining": quota}


def _quota_remaining(store: Any) -> int | None:
    from getaway.store import NoData

    try:
        return store.latest_quota()["remaining"]
    except NoData:
        return None


sweep_group = click.Group("sweep", help="Derive and run trip sweeps from the plan.")


@sweep_group.command("plan")
@click.argument("slug")
@map_errors
def _plan_cmd(slug: str) -> None:
    from getaway import prefs

    trip = trips.show(slug)
    specs = derive_specs(trip, prefs.show())
    labels = [spec["label"] for spec in specs]
    if trip["plan"].get("hybrid"):
        labels.append("onward")
    emit({"labels": labels, "specs": specs})


@sweep_group.command("run")
@click.argument("slug")
@click.argument("label")
@click.option("--refresh", is_flag=True)
@map_errors
def _run_cmd(slug: str, label: str, refresh: bool) -> None:
    try:
        emit(run(slug, label, refresh=refresh))
    except AuthError as err:
        from getaway.constants import EXIT_AUTH

        click.echo(str(err), err=True)
        raise SystemExit(EXIT_AUTH) from err
