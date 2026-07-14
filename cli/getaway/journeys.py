"""Journey composition — the expand-node executor (``getaway expand run <slug>``).

Reads the outbound (and, on round trips, return) leg shortlists, expands each candidate's
concrete ``/trips/{id}`` itinerary (cache-first; a miss spends seats.aero quota through
:class:`SeatsClient`), pairs outbound with return legs into whole journeys, and — for each —
consumes :func:`fit.journey_fit` for fit facts and mandatory preference misses, then computes
per-program cost vectors and journey-level seat sufficiency. A journey with a *known*
insufficient leg gates out (seat sufficiency is judged on the live-expanded row, never the
cached ``/search`` teaser); ``unknown`` stays visible with a verification warning. Outbounds
with no bookable return surface as a separate lead class, never as degenerate journeys.

Hybrid journeys are composed here too, in the same journeys list — never a separate class. A
hybrid outbound side is a gateway award leg plus an onward leg typed ``award`` or ``cash`` (a
priced bridge from ``bridge.json``); the effective destination is the last pre-return leg. Its
onward.json + bridge.json reads are optional: an absent or failed bridge yields zero cash
hybrids without blocking direct journeys.

Pairing happens here, before ranking. Composition is deterministic CLI code — no agent prompts.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import fit, prefs, trips
from getaway.constants import (
    CABIN_PREFIX,
    DEFAULT_QUOTA_FLOOR,
    EXIT_AUTH,
    EXIT_NEGATIVE,
    NODE_TTL_HOURS,
)
from getaway.paths import cache_db, emit, map_errors, utcnow
from getaway.seats import AuthError, SeatsClient
from getaway.store import NoData, QuotaFloorError, connect

Row = dict[str, Any]
Detail = dict[str, Any]

_DETAIL_TTL = dt.timedelta(hours=NODE_TTL_HOURS["expand"])
_SWEEP_TTL_HOURS = NODE_TTL_HOURS["sweep"]
_DEFAULT_MAX_HYBRIDS = 3


def _shortlist(slug: str, name: str) -> dict | None:
    if name in trips.artifact_list(slug):
        return json.loads(trips.artifact_read(slug, name))
    return None


def _sweep_provenance(slug: str, name: str) -> dict | None:
    if name in trips.artifact_list(slug):
        return json.loads(trips.artifact_read(slug, name))["provenance"]
    return None


def _detail_matches_cabin(detail: Detail, letter: str) -> bool:
    return all(seg["cabin"] == letter for seg in detail["segments"])


def _cache_age_hours(fetched_at: str | None, now: dt.datetime) -> float | None:
    if fetched_at is None:
        return None
    return round((now - dt.datetime.fromisoformat(fetched_at)).total_seconds() / 3600, 2)


class _Expander:
    """Expands (id, cabin) shortlist candidates to concrete itineraries, cache-first.

    Memoizes per-run by (id, cabin) so the same availability expanded in two cabins never
    collides on the store's id-only detail cache. Raises :class:`QuotaFloorError` upward the
    first time a live fetch would cross the floor — the caller records the quota stop.
    """

    def __init__(self, store: Any, floor: int, now: dt.datetime) -> None:
        self._store = store
        self._floor = floor
        self._now = now
        self._client: SeatsClient | None = None
        self._memo: dict[tuple[str, str], tuple[Detail | None, str | None]] = {}

    def _seats_client(self) -> SeatsClient:  # built on first miss — a fully cached run needs no key
        if self._client is None:
            self._client = SeatsClient(self._store, floor=self._floor)
        return self._client

    def expand(self, cid: str, letter: str) -> tuple[Detail | None, str | None]:
        """Return ``(detail, fetched_at)`` for a candidate, or ``(None, None)`` when the
        availability has no bookable itinerary in ``letter``."""
        key = (cid, letter)
        if key in self._memo:
            return self._memo[key]
        cached = self._store.trip_detail_get(cid, fresh_within=_DETAIL_TTL)
        if cached is not None and not cached["segments"]:  # known-empty cache reads as no itinerary
            self._memo[key] = (None, None)
            return None, None
        if cached is not None and _detail_matches_cabin(cached, letter):
            result: tuple[Detail | None, str | None] = (cached, None)
            self._memo[key] = result
            return result
        try:
            detail = self._seats_client().trip_detail(cid, letter)
        except NoData:
            self._memo[key] = (None, None)
            return None, None
        self._store.trip_detail_put(cid, detail)
        result = (detail, self._now.isoformat())
        self._memo[key] = result
        return result


def _leg(role: str, cand: Row, detail: Detail, fetched_at: str | None) -> dict:
    return {
        "role": role,
        "id": cand["id"],
        "cabin": cand["cabin"],
        "source": cand["source"],
        "mode": "award",
        "detail": detail,
        "fetched_at": fetched_at,
        "soft": cand.get("soft", False),
        "airlines": cand.get("airlines", ""),
    }


def _cash_leg(role: str, quote: dict, date: str) -> dict:
    cash = {
        "amount_cents": round(quote["price"] * 100),
        "currency": quote["currency"],
        "duration_minutes": quote["duration_minutes"],
        "stops": quote["stops"],
        "connections": quote["connections"],
        "airline": quote["airline"],
        "flight_number": quote["flight_number"],
        "depart_date": date,
    }
    cash["departs_local"] = quote["departs_local"]
    cash["arrives_local"] = quote["arrives_local"]
    return {
        "role": role,
        "id": f"cash:{quote['gateway']}:{quote['onward_dest']}:{date}",
        "cabin": quote["cabin"],
        "source": None,
        "mode": "cash",
        "origin": quote["gateway"],
        "dest": quote["onward_dest"],
        "cash": cash,
    }


def _journey_id(legs: list[dict]) -> str:
    return "|".join(f"{leg['role']}:{leg['id']}:{leg['cabin']}" for leg in legs)


def _endpoints(detail: Detail) -> tuple[str, str, str, str]:
    segs = detail["segments"]
    return segs[0]["origin"], segs[-1]["dest"], segs[0]["departs_local"], segs[-1]["arrives_local"]


def _leg_airports(leg: dict) -> tuple[str, str]:
    if leg.get("mode") == "cash":
        return leg["origin"], leg["dest"]
    origin, dest, _, _ = _endpoints(leg["detail"])
    return origin, dest


def _transit_points(legs: list[dict]) -> list[str]:
    points: list[str] = []
    for leg in legs:
        if leg.get("mode") == "cash":
            points.extend(leg["cash"]["connections"])  # airside stops inside a priced cash hop
        else:
            segments = leg["detail"]["segments"]
            for arriving, departing in zip(segments, segments[1:]):
                points.extend((arriving["dest"], departing["origin"]))
    for arriving, departing in zip(legs, legs[1:]):
        if departing["role"] == "return":
            continue
        _, arrival_airport = _leg_airports(arriving)
        departure_airport, _ = _leg_airports(departing)
        points.extend((arrival_airport, departure_airport))
    return points


def _side_endpoints(side: list[dict]) -> tuple[str, str, str, str]:
    """(origin, effective_dest, first_departure, last_arrival) of an outbound side.

    Origin and departure come from the first (award gateway) leg; the destination and arrival
    come from the last pre-return leg (onward_dest) — a cash onward leg's real arrival clock is
    used the same as an award leg's.
    """
    first, last = side[0], side[-1]
    origin, _, dep, _ = _endpoints(first["detail"])
    if last.get("mode") == "cash":
        return origin, last["dest"], dep, last["cash"]["arrives_local"]
    _, dest, _, arr = _endpoints(last["detail"])
    return origin, dest, dep, arr


def _structural_ok(side: list[dict], ret: dict, same_airport: bool) -> bool:
    _, _, ret_dep, _ = _endpoints(ret["detail"])
    _, _, _, last_arr = _side_endpoints(side)
    if same_airport:  # one airport, one timezone — a full timestamp compare is safe
        return dt.datetime.fromisoformat(ret_dep) > dt.datetime.fromisoformat(last_arr)
    # open jaw: a surface hop of unknown timing sits between the legs — compare dates only
    return dt.date.fromisoformat(ret_dep[:10]) >= dt.date.fromisoformat(last_arr[:10])


def _cost(fit_facts: dict, legs: list[dict]) -> dict:
    mileage = fit_facts["mileage"]
    taxes = [
        {
            "leg_role": leg["role"],
            "amount": leg["detail"]["total_taxes"],
            "currency": leg["detail"]["taxes_currency"],
        }
        for leg in legs
        if leg.get("mode") != "cash" and "total_taxes" in leg["detail"]
    ]
    cash = [
        {
            "leg_role": leg["role"],
            "amount_cents": leg["cash"]["amount_cents"],
            "currency": leg["cash"]["currency"],
            "duration_minutes": leg["cash"]["duration_minutes"],
            "airline": leg["cash"]["airline"],
        }
        for leg in legs
        if leg.get("mode") == "cash"
    ]
    return {"mileage": mileage, "cash": cash, "taxes": taxes, "unpriced": []}


def _seat_rollup(fit_facts: dict) -> str:
    states = {
        leg["seat_sufficiency"]["state"]
        for leg in fit_facts["legs"]
        if "seat_sufficiency" in leg  # cash legs carry no seats.aero row to judge
    }
    if "insufficient" in states:
        return "insufficient"
    if "unknown" in states:
        return "unknown"
    return "sufficient"


def _compose(
    trip: dict,
    prefs_doc: dict,
    outbound_sides: list[list[dict]],
    return_legs: list[dict],
    now: Callable[[], dt.datetime],
) -> tuple[list[dict], list[str], list[dict]]:
    """Pair expanded outbound sides into journeys. Returns ``(journeys, paired_ids, gated)``.

    An outbound side is one or more pre-return legs — a direct outbound, or a gateway award plus
    a typed onward leg for a hybrid. One-way: each side is a journey. Round trip / open jaw: each
    side pairs with every structurally valid return sharing its effective destination (or a
    declared override origin). A journey whose live rows show a known-insufficient leg gates out;
    ``unknown`` stays visible. ``paired_ids`` tracks direct outbound ids only, for lead surfacing.
    """
    plan = trip["plan"]
    one_way = trips._trip_type(plan) == "one_way"
    override = plan.get("return") or {}
    override_origins = set(override.get("origins") or [])
    avoid_transit = set(prefs_doc["avoid_transit"])

    journeys: list[dict] = []
    gated: list[dict] = []
    paired: set[str] = set()

    for side in outbound_sides:
        _, side_dest, _, _ = _side_endpoints(side)
        candidate_legs: list[list[dict]] = []
        if one_way:
            candidate_legs.append(list(side))
        else:
            for ret in return_legs:
                ret_origin, _, _, _ = _endpoints(ret["detail"])
                same_airport = ret_origin == side_dest
                if not (same_airport or ret_origin in override_origins):
                    continue
                if not _structural_ok(side, ret, same_airport):
                    continue
                candidate_legs.append([*side, ret])
        for legs in candidate_legs:
            jid = _journey_id(legs)
            avoided = next((code for code in _transit_points(legs) if code in avoid_transit), None)
            if avoided is not None:
                gated.append({"journey_id": jid, "reason": f"transits {avoided}, which you avoid"})
                continue
            fitted = fit.journey_fit(trip, prefs_doc, legs, now)
            sufficiency = _seat_rollup(fitted["fit_facts"])
            if sufficiency == "insufficient":
                gated.append(
                    {"journey_id": jid, "reason": "a leg's live seats are below the party"}
                )
                continue
            if len(side) == 1:  # direct outbound — hybrid gateways never surface as leads
                paired.add(side[0]["id"])
            journeys.append(
                {
                    "id": jid,
                    "kind": _journey_kind(legs, one_way),
                    "legs": legs,
                    "fit_facts": fitted["fit_facts"],
                    "preference_misses": fitted["preference_misses"],
                    "cost": _cost(fitted["fit_facts"], legs),
                    "seat_sufficiency": sufficiency,
                }
            )
    return journeys, sorted(paired), gated


def _journey_kind(legs: list[dict], one_way: bool) -> str:
    onward = next((leg for leg in legs if leg["role"] == "onward"), None)
    if onward is not None:
        return "gateway_cash" if onward.get("mode") == "cash" else "gateway_award"
    ret = next((leg for leg in legs if leg["role"] == "return"), None)
    if one_way or ret is None:
        return "one_way"
    _, ob_dest, _, _ = _endpoints(legs[0]["detail"])
    ret_origin, _, _, _ = _endpoints(ret["detail"])
    return "round_trip" if ret_origin == ob_dest else "open_jaw"


def _unpaired_leads(
    outbound_legs: list[dict],
    paired: set[str],
    return_states: dict,
    return_prov: dict | None,
    now: dt.datetime,
) -> list[dict]:
    searched_at = return_prov["fetched_at"] if return_prov else None
    age = _cache_age_hours(searched_at, now)
    expired = age is not None and age > _SWEEP_TTL_HOURS
    leads: list[dict] = []
    seen: set[str] = set()
    for ob in outbound_legs:
        if ob["id"] in paired or ob["id"] in seen:
            continue
        seen.add(ob["id"])
        _, dest, _, _ = _endpoints(ob["detail"])
        state = dict(return_states.get(dest, {"state": "not_run", "reason": "no_return_search"}))
        if state.get("state") == "searched_empty" and expired:
            state = {"state": "searched_empty", "verification": "unverified"}
        leads.append(
            {
                "outbound": {
                    "id": ob["id"],
                    "cabin": ob["cabin"],
                    "source": ob["source"],
                    "dest": dest,
                    "mileage": ob["detail"]["mileage"],
                    "detail": ob["detail"],
                },
                "return_search_state": state,
                "searched_at": searched_at,
                "cache_age_hours": age,
            }
        )
    leads.sort(key=lambda lead: lead["outbound"]["mileage"])
    return leads


def _expand_leg_group(
    expander: _Expander, candidates: list[Row], role: str, leg_states: dict
) -> tuple[list[dict], bool]:
    legs: list[dict] = []
    quota_stopped = False
    for i, cand in enumerate(candidates):
        try:
            detail, fetched_at = expander.expand(cand["id"], cand["cabin"])
        except QuotaFloorError:
            quota_stopped = True
            for rest in candidates[i:]:
                leg_states[f"{role}:{rest['id']}:{rest['cabin']}"] = {
                    "state": "not_run",
                    "reason": "quota_floor",
                }
            break
        key = f"{role}:{cand['id']}:{cand['cabin']}"
        if detail is None:
            leg_states[key] = {"state": "failed", "reason": "no_itinerary_in_cabin"}
            continue
        leg_states[key] = {"state": "expanded"}
        legs.append(_leg(role, cand, detail, fetched_at))
    return legs, quota_stopped


def _hybrid_specs(slug: str, gw_doc: dict, max_hybrids: int) -> list[dict]:
    """Cheap-ranked hybrid outbound-side specs, capped before any detail is expanded.

    Reads onward.json (minima + bridge_pairs) and bridge.json (cash quotes) — both optional; an
    absent or failed bridge yields zero cash hybrids. A two-award onward alternative only surfaces
    where a priced cash bridge also exists for the pair (the cash quote is what it competes with).
    """
    onward_doc = _shortlist(slug, "legs/outbound/onward.json")
    if onward_doc is None:
        return []
    bridge_doc = _shortlist(slug, "legs/outbound/bridge.json") or {"quotes": []}
    bridge_by_pair = {(q["gateway"], q["onward_dest"], q["date"]): q for q in bridge_doc["quotes"]}
    minima_by_key = {
        (m["gateway"], m["onward_dest"], m["date"], m["cabin"]): m for m in onward_doc["minima"]
    }
    gateway_by_dest: dict[str, Row] = {}
    for cand in gw_doc["candidates"]:  # candidates are already ordered best-first
        gateway_by_dest.setdefault(cand["dest"], cand)

    specs: list[dict] = []
    for pair in onward_doc["bridge_pairs"]:
        gateway, dest, date = pair["gateway"], pair["onward_dest"], pair["date"]
        award = gateway_by_dest.get(gateway)
        cash = bridge_by_pair.get((gateway, dest, date))
        if award is None or cash is None:
            continue
        specs.append(
            {
                "award": award,
                "onward": {"mode": "cash", "quote": cash, "date": date},
                "miles": award["mileage"],
                "cash_cents": round(cash["price"] * 100),
            }
        )
        onward_award = minima_by_key.get((gateway, dest, date, cash["cabin"]))
        if onward_award is not None:
            specs.append(
                {
                    "award": award,
                    "onward": {"mode": "award", "cand": onward_award, "date": date},
                    "miles": award["mileage"] + onward_award["mileage"],
                    "cash_cents": 0,
                }
            )
    specs.sort(key=lambda s: (s["miles"], s["cash_cents"]))
    return specs[:max_hybrids]


def _expand_hybrid_sides(
    expander: _Expander, specs: list[dict], leg_states: dict
) -> tuple[list[list[dict]], bool]:
    sides: list[list[dict]] = []
    for spec in specs:
        award = spec["award"]
        try:
            gw_detail, gw_fetched = expander.expand(award["id"], award["cabin"])
        except QuotaFloorError:
            return sides, True
        gw_key = f"gateway:{award['id']}:{award['cabin']}"
        if gw_detail is None:
            leg_states[gw_key] = {"state": "failed", "reason": "no_itinerary_in_cabin"}
            continue
        leg_states[gw_key] = {"state": "expanded"}
        legs = [_leg("outbound", award, gw_detail, gw_fetched)]
        onward = spec["onward"]
        if onward["mode"] == "cash":
            legs.append(_cash_leg("onward", onward["quote"], onward["date"]))
        else:
            cand = onward["cand"]
            try:
                on_detail, on_fetched = expander.expand(cand["id"], CABIN_PREFIX[cand["cabin"]])
            except QuotaFloorError:
                return sides, True
            on_key = f"onward:{cand['id']}:{cand['cabin']}"
            if on_detail is None:
                leg_states[on_key] = {"state": "failed", "reason": "no_itinerary_in_cabin"}
                continue
            leg_states[on_key] = {"state": "expanded"}
            legs.append(_leg("onward", cand, on_detail, on_fetched))
        sides.append(legs)
    return sides, False


def run(
    slug: str,
    quota_floor: int = DEFAULT_QUOTA_FLOOR,
    now: Callable[[], dt.datetime] = utcnow,
) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    one_way = trips._trip_type(plan) == "one_way"
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, "expand")

    ob_doc = _shortlist(slug, "legs/outbound/shortlist.json")
    ret_doc = None if one_way else _shortlist(slug, "legs/return/shortlist.json")
    has_gateway = bool(plan.get("hybrid"))
    gw_doc = _shortlist(slug, "legs/outbound/shortlist-gateway.json") if has_gateway else None

    store = connect(cache_db(), now=now)
    expander = _Expander(store, quota_floor, now())
    leg_states: dict = {}

    outbound_legs, quota_stopped = _expand_leg_group(
        expander, ob_doc["candidates"] if ob_doc else [], "outbound", leg_states
    )
    return_legs: list[dict] = []
    if ret_doc is not None and not quota_stopped:
        return_legs, quota_stopped = _expand_leg_group(
            expander, ret_doc["candidates"], "return", leg_states
        )
    outbound_sides: list[list[dict]] = [[leg] for leg in outbound_legs]
    if gw_doc is not None and not quota_stopped:
        max_hybrids = plan["hybrid"].get("max_hybrids", _DEFAULT_MAX_HYBRIDS)
        specs = _hybrid_specs(slug, gw_doc, max_hybrids)
        hybrid_sides, quota_stopped = _expand_hybrid_sides(expander, specs, leg_states)
        outbound_sides.extend(hybrid_sides)

    journeys, paired, gated = _compose(trip, prefs_doc, outbound_sides, return_legs, now)

    outbound_states = ob_doc["search_states"] if ob_doc else {}
    return_states = ret_doc["search_states"] if ret_doc else {}
    unpaired = (
        []
        if one_way
        else _unpaired_leads(
            outbound_legs,
            set(paired),
            return_states,
            _sweep_provenance(slug, "legs/return/sweep.json"),
            now(),
        )
    )

    doc = {
        "journeys": journeys,
        "unpaired_outbounds": unpaired,
        "gated": gated,
        "search_states": {"outbound": outbound_states, "return": return_states},
        "leg_states": leg_states,
        "provenance": {"fetched_at": now().isoformat(), "quota_stopped": quota_stopped},
    }
    trips.artifact_write(slug, "expand.json", json.dumps(doc, separators=(",", ":")))
    if quota_stopped:
        # The phase is incomplete: leave the node unstamped (a later run resumes cache-first) and
        # exit 1 so the walker reads a quota stop, distinct from a data failure.
        raise QuotaFloorError(
            f"seats.aero quota floor {quota_floor} reached while expanding {slug!r}: "
            "wrote partial journeys, some legs not_run{quota_floor}"
        )
    trips.phase_done(slug, "expand", inputs_fp=inputs_fp, now=now)
    return {
        "journeys": len(journeys),
        "unpaired": len(unpaired),
        "gated": len(gated),
    }


expand_group = click.Group("expand", help="Expand and compose leg shortlists into journeys.")


@expand_group.command("run")
@click.argument("slug")
@click.option("--quota-floor", type=int, default=DEFAULT_QUOTA_FLOOR)
@map_errors
def _run_cmd(slug: str, quota_floor: int) -> None:
    try:
        emit(run(slug, quota_floor=quota_floor))
    except AuthError as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_AUTH) from err
    except QuotaFloorError as err:
        click.echo(str(err), err=True)
        raise SystemExit(EXIT_NEGATIVE) from err
