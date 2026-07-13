import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import afford, prefs, registry, trips
from getaway.constants import MILEAGE_BAND
from getaway.paths import UsageError, cache_db, emit, map_errors, utcnow
from getaway.store import connect

VERDICT_RANK = {"promote": -1, "neutral": 0, "demote": 1}
CREDIT_EXPIRY_DAYS = 90
_BAND_NUM = 100 + round(MILEAGE_BAND * 100)

Row = dict[str, Any]


def _optional_artifact(slug: str, name: str) -> dict | None:
    if name in trips.artifact_list(slug):
        return json.loads(trips.artifact_read(slug, name))
    return None


def _documents_present(prefs_doc: dict) -> bool:
    docs = prefs_doc["documents"]
    return any(docs[section] for section in ("passports", "residency", "visas"))


def _balances_configured(prefs_doc: dict) -> bool:
    balances = prefs_doc["balances"]
    return bool(balances["programs"] or balances["transferable"])


def _shortfall_exists(prefs_doc: dict, slug: str | None) -> bool:
    doc = _optional_artifact(slug, "shortlist.json") if slug else None
    if doc is None:
        return _balances_configured(prefs_doc)
    programs = prefs_doc["balances"]["programs"]
    return any(cand["mileage"] > programs.get(cand["source"], 0) for cand in doc["candidates"])


def _activation(fid: str, trip: dict, prefs_doc: dict, slug: str | None) -> tuple[bool, str]:
    cabin = trip["cabin"]
    plan = trip["plan"]
    if fid in ("affordability", "airline_preference", "layovers"):
        return True, "always active"
    if fid == "departure_days":
        active = bool(prefs_doc["departure_days"])
        return active, "prefs.departure_days set" if active else "no departure days on file"
    if fid in ("seat_quality", "cash_anomaly"):
        active = cabin in ("business", "first")
        return active, f"{cabin} cabin" if active else "economy/premium cabin"
    if fid == "transit_risk":
        active = _documents_present(prefs_doc)
        return active, "documents on file" if active else "no documents on file"
    if fid == "return_viability":
        active = bool(plan.get("round_trip"))
        return active, "round-trip ask" if active else "one-way ask"
    if fid == "destination_context":
        active = bool(trip["vibe"])
        return active, "vibe set" if active else "no vibe set"
    if fid == "status_earning":
        active = bool(prefs_doc["status_goals"])
        return active, "status goals on file" if active else "no status goals"
    if fid == "trip_credits":
        active = bool(prefs_doc["credits"])
        return active, "credits on file" if active else "no credits on file"
    if fid == "points_purchase":
        active = _shortfall_exists(prefs_doc, slug)
        return active, "balance shortfall exists" if active else "balances cover candidates"
    raise UsageError(f"unknown factor id {fid!r}")


def derive_profile(trip: dict, prefs_doc: dict, slug: str | None = None) -> dict:
    profile = {}
    for factor in registry.factors():
        fid = factor["id"]
        active, why = _activation(fid, trip, prefs_doc, slug)
        profile[fid] = {"active": active, "priority": factor["default_tier"], "why": why}
    return profile


def _status_earning_fact(program: str, prefs_doc: dict) -> dict:
    data = registry.status_earning().get(program)
    goals = {g["program"] for g in prefs_doc["status_goals"]}
    return {
        "program": program,
        "matches_goal": program in goals,
        "earns_on_redemption": data["earns_on_redemption"] if data else None,
        "metric": data["metric"] if data else None,
        "note": data["note"] if data else None,
    }


def _is_expiring(expires: str, now: Callable[[], dt.datetime]) -> bool:
    return dt.date.fromisoformat(expires) <= now().date() + dt.timedelta(days=CREDIT_EXPIRY_DAYS)


def _trip_credits_fact(cand: Row, prefs_doc: dict, now: Callable[[], dt.datetime]) -> list[dict]:
    airlines = {a for a in cand["airlines"].split(", ") if a}
    program = cand["source"]
    today = now().date()
    matches = []
    for credit in prefs_doc["credits"]:
        if dt.date.fromisoformat(credit["expires"]) < today:
            continue
        issuer = credit["issuer"]
        if issuer.lower() == program.lower() or issuer.upper() in airlines:
            matches.append(
                {
                    "id": credit["id"],
                    "issuer": issuer,
                    "amount": credit["amount"],
                    "currency": credit["currency"],
                    "expires": credit["expires"],
                    "expiring": _is_expiring(credit["expires"], now),
                }
            )
    return matches


def _facts(
    cand: Row, prefs_doc: dict, active: set[str], now: Callable[[], dt.datetime]
) -> dict:
    program = cand["source"]
    mileage = cand["mileage"]
    facts: dict[str, Any] = {"afford": afford.afford(program, mileage, prefs_doc)}
    if "status_earning" in active:
        facts["status_earning"] = _status_earning_fact(program, prefs_doc)
    if "points_purchase" in active:
        facts["points_purchase"] = afford.afford(
            program, mileage, prefs_doc, include_purchase=True
        )["purchase"]
    if "trip_credits" in active:
        facts["trip_credits"] = _trip_credits_fact(cand, prefs_doc, now)
    return facts


def _tiers(trip: dict) -> dict:
    tiers = {factor["id"]: factor["default_tier"] for factor in registry.factors()}
    for fid, spec in trip.get("judgment", {}).get("factors", {}).items():
        tiers[fid] = spec["priority"]
    return tiers


def _verdict_score(factors_map: dict, factor_ids: set[str]) -> int:
    return sum(
        VERDICT_RANK[factors_map.get(fid, {}).get("verdict", "neutral")] for fid in factor_ids
    )


def _affordability_verdict(fact: dict) -> str:
    if fact["covered"]:
        return "promote"
    if any(path["covers"] for path in fact["transfer_paths"]):
        return "neutral"
    return "demote"


def _deterministic_verdicts(cand: Row, facts: dict, active: set[str]) -> dict:
    # points_purchase and departure_days stay note-tier annotations, never verdicts.
    verdicts = {"affordability": _affordability_verdict(facts["afford"])}
    if cand["soft"]:
        verdicts["airline_preference"] = "demote"
    if "status_earning" in active:
        fact = facts["status_earning"]
        if fact["matches_goal"] and fact["earns_on_redemption"]:
            verdicts["status_earning"] = "promote"
    if "trip_credits" in active and facts["trip_credits"]:
        verdicts["trip_credits"] = "promote"
    return {fid: {"verdict": verdict} for fid, verdict in verdicts.items()}


def _infeasible(record: Row | None, party: int, ceiling: int | None) -> str | None:
    if record is None:
        return None
    seats = record.get("seats")
    if seats and seats < party:  # absent or zero seat data passes
        return f"expanded seats {seats} below party {party}"
    if ceiling is not None and record["mileage"] > ceiling:
        return f"expanded mileage {record['mileage']} above ceiling {ceiling}"
    return None


def _reorder(entries: list[Row], tiers: dict, active: set[str]) -> list[Row]:
    entries.sort(key=lambda e: e["_mileage"])
    primary = {fid for fid, tier in tiers.items() if tier == "primary" and fid in active}
    secondary = {fid for fid, tier in tiers.items() if tier == "secondary" and fid in active}
    result: list[Row] = []
    i = 0
    n = len(entries)
    while i < n:
        band_start = entries[i]["_mileage"]
        j = i
        while j < n and entries[j]["_mileage"] * 100 <= band_start * _BAND_NUM:
            j += 1
        band = entries[i:j]
        band.sort(
            key=lambda e: (
                _verdict_score(e["_verdicts"], primary),
                1 if e["_product"] == "barely" else 0,
                _verdict_score(e["_verdicts"], secondary),
                e["_mileage"],
            )
        )
        result.extend(band)
        i = j
    return result


def rank(slug: str, now: Callable[[], dt.datetime] = utcnow) -> list[dict]:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    party = trip["party"]
    ceiling = plan.get("mileage_ceiling")
    profile = derive_profile(trip, prefs_doc, slug=slug)
    active = {fid for fid, spec in profile.items() if spec["active"]}
    tiers = _tiers(trip)
    shortlist_doc = json.loads(trips.artifact_read(slug, "shortlist.json"))
    expand = _optional_artifact(slug, "expand.json") or {}
    assess = _optional_artifact(slug, "assess.json") or {}

    entries: list[Row] = []
    dropped: list[Row] = []
    for row in shortlist_doc["candidates"]:
        record = expand.get(row["id"])
        # Ranking currency is the bookable trip mileage once a candidate is expanded; an
        # unexpanded candidate (quota-low trims the Expand phase) ranks on its sweep mileage.
        if record:
            cand = {**row, "mileage": record["mileage"], "sweep_mileage": row["mileage"]}
        else:
            cand = row
        # The shortlist's two hard feasibility constraints, re-applied against expanded truth.
        reason = _infeasible(record, party, ceiling)
        if reason is not None:
            dropped.append({"candidate": cand, "reason": reason})
            continue
        facts = _facts(cand, prefs_doc, active, now)
        judged = assess.get(cand["id"], {})
        entries.append(
            {
                "candidate": cand,
                "factors": judged,
                "facts": facts,
                "_verdicts": {**_deterministic_verdicts(cand, facts, active), **judged},
                "_product": (record or {}).get("product"),
                "_mileage": cand["mileage"],
            }
        )
    ranked = _reorder(entries, tiers, active)[: plan.get("max_finalists", 6)]
    out = [
        {"candidate": e["candidate"], "factors": e["factors"], "facts": e["facts"]}
        for e in ranked
    ]
    doc = {"ranked": out, "dropped": dropped}
    trips.artifact_write(slug, "rank.json", json.dumps(doc, separators=(",", ":")))
    deps = trips.existing_artifacts(slug, ["shortlist.json", "expand.json", "assess.json"])
    trips.phase_done(slug, "rank", deps, now=now)
    return out


def _compose_hybrids(slug: str, trip: dict, store: Any) -> list[dict]:
    hybrid = trip["plan"]["hybrid"]
    max_hybrids = hybrid.get("max_hybrids", 3)
    gateway_doc = json.loads(trips.artifact_read(slug, "shortlist-gateway.json"))
    gateway_by_dest: dict[str, Row] = {}
    for cand in gateway_doc["candidates"]:  # candidates are already ordered best-first
        gateway_by_dest.setdefault(cand["dest"], cand)
    onward_doc = json.loads(trips.artifact_read(slug, "onward.json"))
    minima_by_key = {
        (m["gateway"], m["onward_dest"], m["date"], m["cabin"]): m for m in onward_doc["minima"]
    }
    bridge_doc = _optional_artifact(slug, "bridge.json") or {"quotes": []}
    bridge_by_pair = {(q["gateway"], q["onward_dest"]): q for q in bridge_doc["quotes"]}

    composed: list[dict] = []
    for pair in onward_doc["bridge_pairs"]:
        gateway, dest = pair["gateway"], pair["onward_dest"]
        award = gateway_by_dest.get(gateway)
        if award is None:
            continue
        cash = bridge_by_pair.get((gateway, dest))
        if cash is None:
            continue
        composed.append(
            {
                "kind": "gateway-cash",
                "gateway": gateway,
                "onward_dest": dest,
                "award": award,
                "onward": {"mode": "cash", **cash},
            }
        )
        onward_award = minima_by_key.get((gateway, dest, pair["date"], cash["cabin"]))
        if onward_award is not None:
            composed.append(
                {
                    "kind": "two-award",
                    "gateway": gateway,
                    "onward_dest": dest,
                    "award": award,
                    "onward": {"mode": "award", **onward_award},
                }
            )

    def total_miles(h: dict) -> int:
        onward = h["onward"]
        return h["award"]["mileage"] + (onward["mileage"] if onward["mode"] == "award" else 0)

    def cash_minor(h: dict) -> int:
        onward = h["onward"]
        return round(onward["price"] * 100) if onward["mode"] == "cash" else 0

    composed.sort(key=lambda h: (total_miles(h), cash_minor(h)))
    composed = composed[:max_hybrids]
    for h in composed:
        h["award_detail"] = store.trip_detail_get(h["award"]["id"])
        if h["onward"]["mode"] == "award":
            h["onward_detail"] = store.trip_detail_get(h["onward"]["id"])
    return composed


def finalize(slug: str, now: Callable[[], dt.datetime] = utcnow) -> dict:
    trip = trips.show(slug)
    plan = trip["plan"]
    ranked = json.loads(trips.artifact_read(slug, "rank.json"))["ranked"]
    store = connect(cache_db(), now=now)
    directs = [
        {
            "kind": "direct",
            "candidate": entry["candidate"],
            "factors": entry["factors"],
            "facts": entry["facts"],
            "detail": store.trip_detail_get(entry["candidate"]["id"]),
        }
        for entry in ranked
    ]
    hybrids = _compose_hybrids(slug, trip, store) if plan.get("hybrid") else []
    doc = {"directs": directs, "hybrids": hybrids}
    trips.artifact_write(slug, "finalists.json", json.dumps(doc, separators=(",", ":")))
    deps = trips.existing_artifacts(
        slug, ["rank.json", "shortlist-gateway.json", "onward.json", "bridge.json"]
    )
    trips.phase_done(slug, "finalize", deps, now=now)
    return doc


@click.command("rank")
@click.argument("slug")
@map_errors
def rank_cmd(slug: str) -> None:
    emit(rank(slug))
