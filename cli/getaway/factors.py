"""Journey ranking and finalist formatting.

Ranking has three stages and never sums verdicts into a score. (1) Cost lane: same-program
journeys band on scalar combined mileage, mixed-program journeys compare as per-program vectors
on a Pareto front — there is no fungible cross-program scalar; a structural primary-clears guard
keeps a journey that clears a primary fit preference from being cost-dominated by one that
misses it. (2) Judgment lane, consumed lexicographically within a cost tier: primary orders,
secondary breaks ties, note annotates and never reorders; unknown is neutral. A factor's tier
folds registry default, then declared preference priority (a fit factor lifts from its mapped
preference keys, strongest of several), then explicit trip judgment override. Guard and trigger
participation is per code, not per factor: a judgment override speaks for all a factor's codes,
otherwise each code's own declared priority, otherwise the factor's registry default. (3) A
stable cost/id tie-break. Preferences never gate; confirmed constraints gate here, while seat
insufficiency is gated upstream at composition. Beyond the presentation cut, assess-picked
notable stretches are joined by a deterministic trigger: each primary fit preference no
finalist or already-surfaced stretch clears surfaces the best-ranked beyond-cut journey that
clears it, skipping journeys availability verification marked gone or degraded.
``finalize`` only formats the board's result classes.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import afford, enhance, fit, prefs, registry, stays, trips
from getaway.constants import CABIN_PREFIX, MILEAGE_BAND, PRESENTATION_LIMIT, cabin_rank
from getaway.paths import UsageError, emit, map_errors, utcnow

VERDICT_RANK = {"promote": -1, "neutral": 0, "demote": 1}
CREDIT_EXPIRY_DAYS = 90
_BAND_NUM = 100 + round(MILEAGE_BAND * 100)
_CASH_AXIS = "$cash"  # a hybrid's cash cost is its own Pareto dimension, never fungible with miles

# Fit factor id → the deterministic preference-miss codes fit.py records for it. Non-fit
# judgment factors have no deterministic codes and never participate in the clears guard.
FIT_MISS_CODES: dict[str, frozenset[str]] = {
    "cabin_fit": frozenset({"cabin"}),
    "window_fit": frozenset({"outbound_departure_window", "return_arrival_by"}),
    "trip_length_fit": frozenset({"trip_length"}),
    "departure_day_fit": frozenset({"departure_days"}),
    "mileage_fit": frozenset({"mileage_target"}),
}
_TIER_STRENGTH = {"primary": 0, "secondary": 1, "note": 2}


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


def _has_monetary_credit(prefs_doc: dict) -> bool:
    return any(i["type"] == "monetary_credit" for i in prefs_doc["travel_instruments"])


def _shortfall_exists(prefs_doc: dict, slug: str | None) -> bool:
    doc = _optional_artifact(slug, "expand.json") if slug else None
    if doc is None:
        return _balances_configured(prefs_doc)
    programs = prefs_doc["balances"]["programs"]
    return any(
        miles > programs.get(program, 0)
        for journey in doc["journeys"]
        for program, miles in journey["cost"]["mileage"]["by_program"].items()
    )


def _activation(fid: str, trip: dict, prefs_doc: dict, slug: str | None) -> tuple[bool, str]:
    cabin = trip["cabin"]
    preferences = trip["plan"].get("preferences", {})
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
    if fid == "destination_context":
        active = bool(trip["vibe"])
        return active, "vibe set" if active else "no vibe set"
    if fid == "status_earning":
        active = bool(prefs_doc["status_goals"])
        return active, "status goals on file" if active else "no status goals"
    if fid == "trip_credits":
        active = _has_monetary_credit(prefs_doc)
        return active, "monetary credit on file" if active else "no monetary credit"
    if fid == "points_purchase":
        active = _shortfall_exists(prefs_doc, slug)
        return active, "balance shortfall exists" if active else "balances cover journeys"
    if fid == "window_fit":
        active = "outbound_departure_window" in preferences or "return_arrival_by" in preferences
        return active, "window preference set" if active else "no window preference"
    if fid == "trip_length_fit":
        active = "trip_length" in preferences
        return active, "trip_length preference set" if active else "no trip_length preference"
    if fid == "departure_day_fit":
        active = "departure_days" in preferences
        return active, "departure_days preference set" if active else "no departure_days preference"
    if fid == "mileage_fit":
        active = "mileage_target" in preferences
        return active, "mileage_target preference set" if active else "no mileage_target preference"
    if fid == "cabin_fit":
        active = "cabin" in preferences
        return active, "cabin preference set" if active else "no cabin preference"
    if fid == "availability_verified":
        active = slug is not None and enhance.present(slug, "verify")
        return active, "verification artifact present" if active else "no verification artifact"
    raise UsageError(f"unknown factor id {fid!r}")


def _declared_tiers(preferences: dict) -> dict[str, str]:
    """Fit-factor tiers declared via ``plan.preferences.<key>.priority``: a factor mapped to
    several preference keys takes the strongest declared priority."""
    declared = {}
    for fid, codes in FIT_MISS_CODES.items():
        priorities = [preferences[code]["priority"] for code in codes if code in preferences]
        if priorities:
            declared[fid] = min(priorities, key=_TIER_STRENGTH.__getitem__)
    return declared


def _tiers(trip: dict) -> dict:
    """Effective factor tiers: registry ``default_tier``, overridden by declared preference
    priorities, overridden by explicit ``judgment.factors`` entries."""
    tiers = {factor["id"]: factor["default_tier"] for factor in registry.factors()}
    tiers |= _declared_tiers(trip["plan"].get("preferences", {}))
    for fid, spec in trip.get("judgment", {}).get("factors", {}).items():
        tiers[fid] = spec["priority"]
    return tiers


def derive_profile(trip: dict, prefs_doc: dict, slug: str | None = None) -> dict:
    tiers = _tiers(trip)
    profile = {}
    for factor in registry.factors():
        fid = factor["id"]
        active, why = _activation(fid, trip, prefs_doc, slug)
        profile[fid] = {"active": active, "priority": tiers[fid], "why": why}
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


def _journey_programs(journey: dict) -> set[str]:
    return {leg["source"].lower() for leg in journey["legs"] if leg.get("source")}


def _journey_airlines(journey: dict) -> set[str]:
    codes: set[str] = set()
    for leg in journey["legs"]:
        codes |= {a for a in leg.get("airlines", "").split(", ") if a}
    return codes


def _trip_credits_fact(
    journey: dict, prefs_doc: dict, now: Callable[[], dt.datetime]
) -> list[dict]:
    """Monetary-credit instruments whose issuer matches a journey program or operating carrier.

    Only the ``monetary_credit`` variant carries an amount and currency; certificates and
    companion fares are other instruments and never surface here.
    """
    today = now().date()
    programs = _journey_programs(journey)
    airlines = {a.upper() for a in _journey_airlines(journey)}
    matches = []
    for instrument in prefs_doc["travel_instruments"]:
        if instrument["type"] != "monetary_credit":
            continue
        if dt.date.fromisoformat(instrument["expires"]) < today:
            continue
        issuer = instrument["issuer"]
        if issuer.lower() in programs or issuer.upper() in airlines:
            matches.append(
                {
                    "id": instrument["id"],
                    "issuer": issuer,
                    "amount": instrument["amount"],
                    "currency": instrument["currency"],
                    "expires": instrument["expires"],
                    "expiring": _is_expiring(instrument["expires"], now),
                }
            )
    return matches


def _afford_fact(journey: dict, prefs_doc: dict) -> dict:
    by_program = {
        program: afford.afford(program, miles, prefs_doc)
        for program, miles in journey["cost"]["mileage"]["by_program"].items()
    }
    return {
        "by_program": by_program,
        "covered": all(a["covered"] for a in by_program.values()),
        "total_shortfall": sum(a["shortfall"] for a in by_program.values()),
    }


def _verification_rows(journey: dict, verify_results: dict) -> list[dict]:
    rows = []
    for leg in journey["legs"]:
        if leg.get("mode") == "cash":
            continue
        result = verify_results.get(f"{leg['id']}:{leg['cabin']}")
        if result is None:
            continue
        rows.append(
            {
                "leg": leg["role"],
                "availability_id": leg["id"],
                "outcome": result["outcome"],
                "checked_at": result["checked_at"],
                "observed": result["observed"],
                "evidence": result["evidence"],
            }
        )
    return rows


def _facts(
    journey: dict,
    prefs_doc: dict,
    active: set[str],
    now: Callable[[], dt.datetime],
    verify_results: dict,
) -> dict:
    by_program = journey["cost"]["mileage"]["by_program"]
    facts: dict[str, Any] = {"afford": _afford_fact(journey, prefs_doc)}
    if "status_earning" in active:
        facts["status_earning"] = [_status_earning_fact(p, prefs_doc) for p in by_program]
    if "points_purchase" in active:
        facts["points_purchase"] = [
            afford.afford(p, m, prefs_doc, include_purchase=True)["purchase"]
            for p, m in by_program.items()
            if m > prefs_doc["balances"]["programs"].get(p, 0)
        ]
    if "trip_credits" in active:
        facts["trip_credits"] = _trip_credits_fact(journey, prefs_doc, now)
    if "availability_verified" in active:
        rows = _verification_rows(journey, verify_results)
        if rows:
            facts["availability_verification"] = rows
    return facts


def _afford_verdict(afford_fact: dict) -> str:
    if afford_fact["covered"]:
        return "promote"
    coverable = all(
        entry["covered"] or any(path["covers"] for path in entry["transfer_paths"])
        for entry in afford_fact["by_program"].values()
    )
    return "neutral" if coverable else "demote"


def _deterministic_verdicts(journey: dict, facts: dict, active: set[str]) -> list[dict]:
    """Verdicts the CLI computes deterministically per journey (no model judgment).

    ``points_purchase`` and ``departure_days`` stay note-tier annotations, never verdicts.
    """
    verdicts = [
        {"factor": "affordability", "leg": None, "verdict": _afford_verdict(facts["afford"])}
    ]
    if any(leg.get("soft") for leg in journey["legs"]):
        verdicts.append({"factor": "airline_preference", "leg": None, "verdict": "demote"})
    if "status_earning" in active and any(
        f["matches_goal"] and f["earns_on_redemption"] for f in facts.get("status_earning", [])
    ):
        verdicts.append({"factor": "status_earning", "leg": None, "verdict": "promote"})
    if "trip_credits" in active and facts.get("trip_credits"):
        verdicts.append({"factor": "trip_credits", "leg": None, "verdict": "promote"})
    for row in facts.get("availability_verification", []):
        if row["outcome"] in ("gone", "degraded"):
            verdicts.append(
                {"factor": "availability_verified", "leg": row["leg"], "verdict": "demote"}
            )
    return verdicts


def _cost_vector(entry: dict) -> dict[str, int]:
    journey = entry["journey"]
    vector = dict(journey["cost"]["mileage"]["by_program"])
    cash_cents = sum(component["amount_cents"] for component in journey["cost"]["cash"])
    if cash_cents:
        vector[_CASH_AXIS] = cash_cents
    return vector


def _clears(entry: dict, codes: frozenset[str]) -> frozenset[str]:
    """The subset of ``codes`` the entry's journey records no deterministic preference miss for."""
    return codes - {miss["code"] for miss in entry["journey"]["preference_misses"]}


def _verified_unavailable(entry: dict) -> bool:
    """Availability verification marked a leg gone or degraded — the deterministic verify-artifact
    signal, never an assess verdict."""
    return any(
        row["outcome"] in ("gone", "degraded")
        for row in entry["facts"].get("availability_verification", [])
    )


def _primary_codes(trip: dict, active: set[str]) -> frozenset[str]:
    """Active fit-preference codes whose per-code effective priority is primary. A
    ``judgment.factors`` override speaks for all of its factor's codes; otherwise a code's own
    declared ``preferences[code].priority`` wins over the factor's registry default — never the
    strongest-of-several factor fold, which serves only the verdict lane."""
    preferences = trip["plan"].get("preferences", {})
    overrides = trip.get("judgment", {}).get("factors", {})
    defaults = {factor["id"]: factor["default_tier"] for factor in registry.factors()}
    codes = []
    for fid, fit_codes in FIT_MISS_CODES.items():
        if fid not in active:
            continue
        for code in fit_codes:
            if fid in overrides:
                priority = overrides[fid]["priority"]
            elif code in preferences:
                priority = preferences[code]["priority"]
            else:
                priority = defaults[fid]
            if priority == "primary":
                codes.append(code)
    return frozenset(codes)


def _dominates(a: dict, b: dict, primary_codes: frozenset[str]) -> bool:
    """Cost domination, guarded by the active primary fit preferences: ``a`` may only dominate
    ``b`` if it clears every primary preference ``b`` clears — a structural set-difference test
    over deterministic preference misses, never a score. Same single program: ``a`` is cheaper
    beyond the band. Otherwise strict Pareto over the union of dimensions — per-program miles
    plus a hybrid's cash cents, each axis compared within itself (a dimension a journey doesn't
    use costs it zero) — so mixed-program, cross-program, and cash-bearing journeys stay
    incomparable and both surface on the front."""
    return _dominates_cleared(a, b, _clears(a, primary_codes), _clears(b, primary_codes))


def _dominates_cleared(
    a: dict, b: dict, a_clears: frozenset[str], b_clears: frozenset[str]
) -> bool:
    if b_clears - a_clears:
        return False
    av, bv = _cost_vector(a), _cost_vector(b)
    if len(av) == 1 and len(bv) == 1 and set(av) == set(bv):
        (a_total,) = av.values()
        (b_total,) = bv.values()
        return a_total * _BAND_NUM < b_total * 100
    programs = set(av) | set(bv)
    le_all = all(av.get(p, 0) <= bv.get(p, 0) for p in programs)
    lt_any = any(av.get(p, 0) < bv.get(p, 0) for p in programs)
    return le_all and lt_any


def _assign_cost_tiers(entries: list[dict], primary_codes: frozenset[str]) -> None:
    cleared = {id(e): _clears(e, primary_codes) for e in entries}
    remaining = list(entries)
    tier = 0
    while remaining:
        front = [
            e
            for e in remaining
            if not any(
                _dominates_cleared(o, e, cleared[id(o)], cleared[id(e)])
                for o in remaining
                if o is not e
            )
        ]
        if not front:  # a domination cycle should be impossible; collapse rather than spin
            front = remaining
        for entry in front:
            entry["_cost_tier"] = tier
        remaining = [e for e in remaining if e not in front]
        tier += 1


def _lane_ranks(entry: dict, lane: set[str]) -> list[int]:
    return [VERDICT_RANK[v["verdict"]] for v in entry["verdicts"] if v["factor"] in lane]


def _lane_key(entry: dict, lane: set[str], width: int) -> tuple[int, ...]:
    ranks = _lane_ranks(entry, lane)
    padded = ranks + [0] * (width - len(ranks))  # absent verdicts read as neutral
    return tuple(sorted(padded, reverse=True))  # worst (demote) compared first


def _tiebreak(entry: dict) -> tuple[tuple[tuple[str, int], ...], str]:
    vector = _cost_vector(entry)  # per-axis, never a cross-program sum — mirrors the cost lane
    return tuple(sorted(vector.items())), entry["journey"]["id"]


def _order(
    entries: list[dict], tiers: dict, active: set[str], primary_codes: frozenset[str]
) -> list[dict]:
    _assign_cost_tiers(entries, primary_codes)
    primary = {fid for fid, tier in tiers.items() if tier == "primary" and fid in active}
    secondary = {fid for fid, tier in tiers.items() if tier == "secondary" and fid in active}
    p_width = max((len(_lane_ranks(e, primary)) for e in entries), default=0)
    s_width = max((len(_lane_ranks(e, secondary)) for e in entries), default=0)
    return sorted(
        entries,
        key=lambda e: (
            e["_cost_tier"],
            _lane_key(e, primary, p_width),
            _lane_key(e, secondary, s_width),
            _tiebreak(e),
        ),
    )


def _entry_out(entry: dict) -> dict:
    return {
        "journey": entry["journey"],
        "facts": entry["facts"],
        "verdicts": entry["verdicts"],
        "cost_tier": entry["_cost_tier"],
    }


def _preference_label(code: str, preferences: dict) -> str:
    if code == "cabin":
        return f"{preferences['cabin']['value']} cabin"
    return code.replace("_", " ")


def _mileage_limit(plan: dict) -> int | None:
    limit = plan.get("constraints", {}).get("mileage_limit")
    return limit["miles"] if limit else None


def _cabin_constraint(plan: dict) -> str | None:
    constraint = plan.get("constraints", {}).get("cabin")
    return constraint["value"] if constraint else None


def _departure_days_constraint(plan: dict) -> set[str] | None:
    constraint = plan.get("constraints", {}).get("departure_days")
    return set(constraint["days"]) if constraint else None


def rank(slug: str, now: Callable[[], dt.datetime] = utcnow) -> list[dict]:
    upstream_fp = trips.capture_upstream_fp(slug, "rank")  # before any input artifact is read
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    profile = derive_profile(trip, prefs_doc, slug=slug)
    active = {fid for fid, spec in profile.items() if spec["active"]}
    tiers = _tiers(trip)
    expand = json.loads(trips.artifact_read(slug, "expand.json"))
    assess = _optional_artifact(slug, "assess.json") or {}
    assess_journeys = assess.get("journeys", {})
    verify_active = "availability_verified" in active
    verify_results = enhance.results_index(slug, "verify") if verify_active else {}
    limit = _mileage_limit(plan)
    cabin_constraint = _cabin_constraint(plan)
    departure_days = _departure_days_constraint(plan)

    entries: list[dict] = []
    dropped: list[dict] = list(expand.get("gated", []))
    for journey in expand["journeys"]:
        facts = _facts(journey, prefs_doc, active, now, verify_results)
        total_miles = sum(journey["cost"]["mileage"]["by_program"].values())
        if limit is not None and total_miles > limit:
            dropped.append(
                {
                    "journey_id": journey["id"],
                    "reason": f"{total_miles} miles over confirmed limit {limit}",
                }
            )
            continue
        if cabin_constraint is not None:
            minimum_cabin = CABIN_PREFIX[cabin_constraint]
            minimum_rank = cabin_rank(minimum_cabin)
            below = next(
                (
                    (leg, segment)
                    for leg in journey["legs"]
                    if leg["mode"] == "award"
                    for segment in leg["detail"]["segments"]
                    if cabin_rank(segment["cabin"]) < minimum_rank
                ),
                None,
            )
            if below is not None:
                leg, segment = below
                dropped.append(
                    {
                        "journey_id": journey["id"],
                        "reason": (
                            f"{leg['role']} award cabin {segment['cabin']} below confirmed cabin "
                            f"{minimum_cabin}"
                        ),
                    }
                )
                continue
        if departure_days is not None:
            departs_local = journey["legs"][0]["detail"]["segments"][0]["departs_local"]
            departure_day = fit._weekday(departs_local)
            if departure_day not in departure_days:
                dropped.append(
                    {
                        "journey_id": journey["id"],
                        "reason": (
                            f"outbound departs {departure_day} outside confirmed departure days "
                            f"{sorted(departure_days)}"
                        ),
                    }
                )
                continue
        judged = assess_journeys.get(journey["id"], {}).get("verdicts", [])
        entries.append(
            {
                "journey": journey,
                "facts": facts,
                "verdicts": _deterministic_verdicts(journey, facts, active) + judged,
            }
        )

    primary_codes = _primary_codes(trip, active)
    ordered = _order(entries, tiers, active, primary_codes)
    ranked = [_entry_out(e) for e in ordered]

    within_cut = {e["journey"]["id"] for e in ordered[:PRESENTATION_LIMIT]}
    by_id = {e["journey"]["id"]: e for e in ordered}
    notable: list[dict] = []
    for stretch in assess.get("notable_stretches", []):
        entry = by_id.get(stretch["journey_id"])
        if entry is not None and stretch["journey_id"] not in within_cut:
            notable.append({**_entry_out(entry), "why": stretch.get("why", "")})

    for code in sorted(primary_codes):
        if any(code in _clears(e, primary_codes) for e in ordered[:PRESENTATION_LIMIT]):
            continue
        if any(code in _clears(n, primary_codes) for n in notable):
            continue
        stretch_entry = next(
            (
                e
                for e in ordered[PRESENTATION_LIMIT:]
                if code in _clears(e, primary_codes) and not _verified_unavailable(e)
            ),
            None,
        )
        if stretch_entry is None:
            continue
        label = _preference_label(code, plan.get("preferences", {}))
        why = f"clears your {label} preference — every finalist misses it"
        notable.append({**_entry_out(stretch_entry), "why": why})

    doc = {"ranked": ranked, "notable_stretches": notable, "dropped": dropped}
    trips.artifact_write(slug, "rank.json", json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, "rank", now=now, upstream_fp=upstream_fp)
    return ranked


def _thread_lodging(doc: dict, plan: dict, slug: str, now: Callable[[], dt.datetime]) -> None:
    """Attach each board journey's walked stay (or its deferral) and mark every unpaired lead's
    lodging deferred — an unpaired outbound has no return leg, so no checkout to search."""
    stays_doc = _optional_artifact(slug, "stays.json") or {"stays": {}}
    for section in ("journeys", "notable_stretches"):
        doc[section] = [
            {**entry, **stays.board_lodging(entry["journey"], plan, stays_doc, now)}
            for entry in doc[section]
        ]
    doc["unpaired_leads"] = [
        {**lead, "lodging_search": stays.unpaired_lodging()} for lead in doc["unpaired_leads"]
    ]


def _thread_verification(doc: dict, slug: str) -> None:
    """Attach each verified lead's rescue result — a background verifier finding return space an
    expired empty search missed. Annotation only: the lead never re-pairs into a journey."""
    results = enhance.results_index(slug, "verify")
    if not results:
        return
    leads = []
    for lead in doc["unpaired_leads"]:
        result = results.get(f"lead:{lead['outbound']['dest']}:{lead['outbound']['cabin']}")
        if result is not None:
            lead = {
                **lead,
                "rescue": {k: result[k] for k in ("outcome", "checked_at", "observed", "evidence")},
            }
        leads.append(lead)
    doc["unpaired_leads"] = leads


def finalize(slug: str, now: Callable[[], dt.datetime] = utcnow) -> dict:
    upstream_fp = trips.capture_upstream_fp(slug, "finalize")  # before any input artifact is read
    trip = trips.show(slug)
    plan = trip["plan"]
    rank_doc = json.loads(trips.artifact_read(slug, "rank.json"))
    expand = _optional_artifact(slug, "expand.json") or {}

    doc = {
        "trip_type": trips._trip_type(plan),
        "journeys": rank_doc["ranked"][:PRESENTATION_LIMIT],
        "notable_stretches": rank_doc["notable_stretches"],
        "unpaired_leads": expand.get("unpaired_outbounds", []),
        "search_states": expand.get("search_states", {}),
        "dropped": rank_doc["dropped"],
    }
    if "lodging" in plan:
        _thread_lodging(doc, plan, slug, now)
    _thread_verification(doc, slug)
    trips.artifact_write(slug, "finalists.json", json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, "finalize", now=now, upstream_fp=upstream_fp)
    return doc


@click.command("rank")
@click.argument("slug")
@map_errors
def rank_cmd(slug: str) -> None:
    emit(rank(slug))
