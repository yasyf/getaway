"""Journey composition — the expand-node executor (``getaway expand run <slug>``).

Reads each leg intent's candidate pool by leg id — award legs from ``legs/<id>/shortlist.json``,
cash legs from ``legs/<id>/bridge.json`` quotes, an either leg from both — then walks the intents
in order building candidate chains where each candidate departs the prior candidate's dest (or the
intent's explicit override origins). Two-leg plans compose exhaustively (bounded by shortlist
budgets, byte-identical to HEAD's pairing loop); a plan of three or more legs cheap-ranks its chains
on ``(miles, cash cents)`` and keeps the top :data:`COMPOSE_BEAM_WIDTH` **before** any ``/trips``
expansion spends quota, disclosing the cut count in ``expand.json`` provenance. Only surviving
chains expand their award legs (cache-first; a miss spends quota through :class:`SeatsClient`).

Continuity refines with :func:`_structural_ok` on the expanded legs: a same-airport boundary
compares full local timestamps with the preference min-connection floor; a stay-marked boundary
bounds the gap to the declared nights; a cross-airport boundary compares dates only (seats.aero
clocks are naive local wall times — never do cross-airport clock math). Each surviving chain draws
fit facts and mandatory preference misses from :func:`fit.journey_fit`, then per-program cost
vectors and journey-level seat sufficiency. A journey with a *known* insufficient leg gates out;
``unknown`` stays visible with a verification warning. Round-trip outbounds with no bookable return
surface as a separate lead class, never as degenerate journeys.

Pairing happens here, before ranking. Composition is deterministic CLI code — no agent prompts.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import fit, prefs, trips
from getaway.constants import (
    COMPOSE_BEAM_WIDTH,
    DEFAULT_QUOTA_FLOOR,
    EXIT_AUTH,
    EXIT_NEGATIVE,
    NODE_TTL_HOURS,
)
from getaway.paths import UsageError, cache_db, emit, map_errors, utcnow
from getaway.seats import AuthError, SeatsClient, itinerary_has_cabin
from getaway.store import NoData, QuotaFloorError, connect

Row = dict[str, Any]
Detail = dict[str, Any]

_DETAIL_TTL = dt.timedelta(hours=NODE_TTL_HOURS["expand"])
_SWEEP_TTL_HOURS = NODE_TTL_HOURS["sweep"]


def _artifact(slug: str, name: str) -> dict | None:
    if name in trips.artifact_list(slug):
        return json.loads(trips.artifact_read(slug, name))
    return None


def _sweep_provenance(slug: str, name: str) -> dict | None:
    doc = _artifact(slug, name)
    return doc["provenance"] if doc is not None else None


def _detail_matches_cabin(detail: Detail, letter: str) -> bool:
    return itinerary_has_cabin([seg["cabin"] for seg in detail["segments"]], letter)


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


def _leg_bounds(leg: dict) -> tuple[str, str, str, str]:
    """(origin, dest, departs_local, arrives_local) of a typed leg, cash or award."""
    if leg.get("mode") == "cash":
        cash = leg["cash"]
        return leg["origin"], leg["dest"], cash["departs_local"], cash["arrives_local"]
    return _endpoints(leg["detail"])


def _leg_airports(leg: dict) -> tuple[str, str]:
    origin, dest, _, _ = _leg_bounds(leg)
    return origin, dest


def _is_return_intent(intent: dict) -> bool:
    """A homeward intent — the last-position ``$origins``-directed leg (doc 49100ad). Structural,
    never a role-string test; used only for veto exemption, not the fit-side gate."""
    return intent.get("dests") == trips.ORIGINS_MARKER


def _transit_points(legs: list[dict], intents: list[dict]) -> list[str]:
    """Airports the journey passes *through* — subject to ``avoid_transit``.

    Within a leg: award segment self-transfers and priced cash-hop connections. Between two legs:
    the arrival/departure airports of a no-stay waypoint boundary. A boundary is an endpoint (not
    transit) when the prior leg marks a stop or the next leg flies home to ``$origins`` — the
    turnaround destination, governed by the destination veto, never ``avoid_transit``.
    """
    points: list[str] = []
    for leg in legs:
        if leg.get("mode") == "cash":
            points.extend(leg["cash"]["connections"])  # airside stops inside a priced cash hop
        else:
            segments = leg["detail"]["segments"]
            for arriving, departing in zip(segments, segments[1:]):
                points.extend((arriving["dest"], departing["origin"]))
    for i in range(len(legs) - 1):
        if "stay_nights" in intents[i] or _is_return_intent(intents[i + 1]):
            continue  # a stop, or the homeward turnaround — an endpoint, not a transit
        _, arrival_airport = _leg_airports(legs[i])
        departure_airport, _ = _leg_airports(legs[i + 1])
        points.extend((arrival_airport, departure_airport))
    return points


def _structural_ok(prior: dict, nxt: dict, prior_intent: dict, min_connection: int) -> bool:
    """Does ``nxt`` continue physically from ``prior``?

    A stay-marked boundary bounds the gap to ``[min .. max]`` nights (date arithmetic — safe across
    timezones). A same-airport boundary compares full local timestamps with the connection floor
    (one airport, one clock). A cross-airport surface hop of unknown timing compares dates only —
    seats.aero timestamps are naive local wall clocks; never subtract them across airports.
    """
    _, prior_dest, _, prior_arr = _leg_bounds(prior)
    nxt_origin, _, nxt_dep, _ = _leg_bounds(nxt)
    stay = prior_intent.get("stay_nights")
    if stay is not None:
        nights = (dt.date.fromisoformat(nxt_dep[:10]) - dt.date.fromisoformat(prior_arr[:10])).days
        return stay["min"] <= nights <= stay["max"]
    if nxt_origin == prior_dest:
        gap = dt.datetime.fromisoformat(nxt_dep) - dt.datetime.fromisoformat(prior_arr)
        return gap >= dt.timedelta(minutes=min_connection)
    return dt.date.fromisoformat(nxt_dep[:10]) >= dt.date.fromisoformat(prior_arr[:10])


def _chain_continuous(legs: list[dict], intents: list[dict], min_connection: int) -> bool:
    return all(
        _structural_ok(legs[i], legs[i + 1], intents[i], min_connection)
        for i in range(len(legs) - 1)
    )


def _journey_shape(legs: list[dict], intents: list[dict]) -> str:
    """A derived shape label, e.g. ``award→cash→award · 2 stays`` — presentation only."""
    modes = "→".join(leg["mode"] for leg in legs)
    stays = sum("stay_nights" in intent for intent in intents)
    if stays:
        return f"{modes} · {stays} stay{'s' if stays != 1 else ''}"
    return modes


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


def _leg_pool(slug: str, leg: dict, shortlist: dict | None) -> list[dict]:
    """One intent's candidate pool: award candidates from its shortlist, cash candidates from its
    priced bridge quotes, an either leg's union of both. Each candidate carries the airport/date it
    chains on and its cheap-rank cost (miles for award, cents for cash)."""
    pool: list[dict] = []
    if leg["mode"] in ("award", "either") and shortlist is not None:
        for cand in shortlist["candidates"]:
            pool.append(
                {
                    "kind": "award",
                    "origin": cand["origin"],
                    "dest": cand["dest"],
                    "date": cand["date"],
                    "cand": cand,
                    "miles": cand["mileage"],
                    "cash_cents": 0,
                }
            )
    if leg["mode"] in ("cash", "either"):
        bridge = _artifact(slug, f"legs/{leg['id']}/bridge.json")
        for quote in bridge["quotes"] if bridge is not None else []:
            pool.append(
                {
                    "kind": "cash",
                    "origin": quote["gateway"],
                    "dest": quote["onward_dest"],
                    "date": quote["date"],
                    "quote": quote,
                    "miles": 0,
                    "cash_cents": round(quote["price"] * 100),
                }
            )
    return pool


def _build_chains(legs: list[dict], pools: list[list[dict]]) -> list[list[dict]]:
    """Every candidate chain over the intents in order: a candidate extends a partial chain iff it
    departs the prior candidate's dest — or, when the intent declares explicit override origins, one
    of those (an open jaw departs where the chain didn't land). Airport anchoring only; timing is a
    :func:`_structural_ok` refinement on the expanded clocks. Shortlist rows carry a departure date
    but no arrival, so a departure-date prefilter would wrongly drop a dateline-crossing pairing
    whose arrival precedes its own departure date — the topology superset never gates on time."""
    chains: list[list[dict]] = [[cand] for cand in pools[0]]
    for i in range(1, len(legs)):
        override = legs[i].get("origins")  # explicit origins REPLACE the chained anchor
        extended: list[list[dict]] = []
        for chain in chains:
            prior = chain[-1]
            for cand in pools[i]:
                anchored = (
                    cand["origin"] in override if override else cand["origin"] == prior["dest"]
                )
                if anchored:
                    extended.append([*chain, cand])
        chains = extended
    chains.sort(key=lambda ch: (sum(c["miles"] for c in ch), sum(c["cash_cents"] for c in ch)))
    return chains


def _compose_chains(
    trip: dict,
    prefs_doc: dict,
    legs: list[dict],
    chains: list[list[dict]],
    expander: _Expander,
    avoid_transit: set[str],
    min_connection: int,
    leg_states: dict,
    now: Callable[[], dt.datetime],
) -> tuple[list[dict], list[dict], set[str], bool]:
    """Expand beam-survivor chains lazily, refine continuity, gate transit/seats, emit journeys.

    Returns ``(journeys, gated, composed_heads, quota_stopped)`` where ``composed_heads`` is the set
    of first-leg award candidate ids that reached a journey — used to surface the rest as leads.
    """
    journeys: list[dict] = []
    gated: list[dict] = []
    composed_heads: set[str] = set()
    for chain in chains:
        legs_typed: list[dict] = []
        quota_stopped = False
        failed = False
        for intent, cand in zip(legs, chain):
            role = intent["id"]
            if cand["kind"] == "cash":
                legs_typed.append(_cash_leg(role, cand["quote"], cand["date"]))
                continue
            row = cand["cand"]
            key = f"{role}:{row['id']}:{row['cabin']}"
            try:
                detail, fetched_at = expander.expand(row["id"], row["cabin"])
            except QuotaFloorError:
                leg_states[key] = {"state": "not_run", "reason": "quota_floor"}
                quota_stopped = True
                break
            if detail is None:
                leg_states[key] = {"state": "failed", "reason": "no_itinerary_in_cabin"}
                failed = True
                break
            leg_states[key] = {"state": "expanded"}
            legs_typed.append(_leg(role, row, detail, fetched_at))
        if quota_stopped:
            return journeys, gated, composed_heads, True
        if failed or not _chain_continuous(legs_typed, legs, min_connection):
            continue
        jid = _journey_id(legs_typed)
        avoided = next(
            (code for code in _transit_points(legs_typed, legs) if code in avoid_transit), None
        )
        if avoided is not None:
            gated.append({"journey_id": jid, "reason": f"transits {avoided}, which you avoid"})
            continue
        fitted = fit.journey_fit(trip, prefs_doc, legs_typed, now)
        sufficiency = _seat_rollup(fitted["fit_facts"])
        if sufficiency == "insufficient":
            gated.append({"journey_id": jid, "reason": "a leg's live seats are below the party"})
            continue
        if chain[0]["kind"] == "award":
            composed_heads.add(chain[0]["cand"]["id"])
        journeys.append(
            {
                "id": jid,
                "kind": _journey_shape(legs_typed, legs),
                "legs": legs_typed,
                "fit_facts": fitted["fit_facts"],
                "preference_misses": fitted["preference_misses"],
                "cost": _cost(fitted["fit_facts"], legs_typed),
                "seat_sufficiency": sufficiency,
            }
        )
    return journeys, gated, composed_heads, False


def _unpaired_leads(
    outbound_legs: list[dict],
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
        if ob["id"] in seen:
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


def _lead_journeys(
    slug: str,
    first_pool: list[dict],
    composed_heads: set[str],
    expander: _Expander,
    leg_states: dict,
    first_leg_id: str,
    return_leg_id: str,
    return_states: dict,
    now: Callable[[], dt.datetime],
) -> tuple[list[dict], bool]:
    """First-leg award candidates that reached no journey, expanded into leads with the downstream
    leg's search state — the ``outbound with no bookable return`` class of a round trip.

    Returns ``(leads, quota_stopped)``. A quota floor crossed here is a quota stop exactly like the
    main expansion path: the breaking candidate records ``not_run``/``quota_floor`` and the caller
    leaves the phase unstamped and raises — the lead expansion is a live-fetch leg, not a footnote.
    """
    outbound_legs: list[dict] = []
    quota_stopped = False
    for cand in first_pool:
        if cand["kind"] != "award" or cand["cand"]["id"] in composed_heads:
            continue
        row = cand["cand"]
        key = f"{first_leg_id}:{row['id']}:{row['cabin']}"
        try:
            detail, fetched_at = expander.expand(row["id"], row["cabin"])
        except QuotaFloorError:
            leg_states[key] = {"state": "not_run", "reason": "quota_floor"}
            quota_stopped = True
            break
        if detail is None:
            leg_states[key] = {"state": "failed", "reason": "no_itinerary_in_cabin"}
            continue
        leg_states.setdefault(key, {"state": "expanded"})
        outbound_legs.append(_leg(first_leg_id, row, detail, fetched_at))
    return_prov = _sweep_provenance(slug, f"legs/{return_leg_id}/sweep.json")
    return _unpaired_leads(outbound_legs, return_states, return_prov, now()), quota_stopped


def run(
    slug: str,
    quota_floor: int = DEFAULT_QUOTA_FLOOR,
    now: Callable[[], dt.datetime] = utcnow,
) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    plan = trip["plan"]
    legs = plan.get("legs")
    if not legs:
        raise UsageError("plan.legs must be a non-empty list before compiling")
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, "expand")
    min_connection = prefs_doc["layovers"]["min_connection_minutes"]
    avoid_transit = set(prefs_doc["avoid_transit"])

    shortlists = {
        leg["id"]: _artifact(slug, f"legs/{leg['id']}/shortlist.json")
        for leg in legs
        if leg["mode"] in ("award", "either")
    }
    pools = [_leg_pool(slug, leg, shortlists.get(leg["id"])) for leg in legs]

    store = connect(cache_db(), now=now)
    expander = _Expander(store, quota_floor, now())
    leg_states: dict = {}

    # Two-leg plans compose exhaustively (HEAD-identical); ≥3-leg plans beam and disclose the cut.
    built = _build_chains(legs, pools)
    beam_cut = 0
    if len(legs) >= 3:
        chains = built[:COMPOSE_BEAM_WIDTH]
        beam_cut = len(built) - len(chains)
    else:
        chains = built
    journeys, gated, composed_heads, quota_stopped = _compose_chains(
        trip, prefs_doc, legs, chains, expander, avoid_transit, min_connection, leg_states, now
    )

    # Leads are the round-trip's unpaired outbounds only; beam-cut ≥3-leg chains are truncation, not
    # leads (partial-chain leads land in P3).
    unpaired: list[dict] = []
    round_trip = (
        len(legs) == 2 and _is_return_intent(legs[-1]) and legs[0]["mode"] in ("award", "either")
    )
    if round_trip and not quota_stopped:
        return_sl = shortlists.get(legs[1]["id"])
        unpaired, lead_quota_stopped = _lead_journeys(
            slug,
            pools[0],
            composed_heads,
            expander,
            leg_states,
            legs[0]["id"],
            legs[1]["id"],
            return_sl["search_states"] if return_sl else {},
            now,
        )
        quota_stopped = quota_stopped or lead_quota_stopped

    search_states = {
        leg["id"]: (shortlists[leg["id"]] or {}).get("search_states", {})
        for leg in legs
        if leg["mode"] in ("award", "either")
    }
    provenance = {"fetched_at": now().isoformat(), "quota_stopped": quota_stopped}
    if beam_cut:
        provenance["truncation"] = {"beam_cut": beam_cut}
    doc = {
        "journeys": journeys,
        "unpaired_outbounds": unpaired,
        "gated": gated,
        "search_states": search_states,
        "leg_states": leg_states,
        "provenance": provenance,
    }
    trips.artifact_write(slug, "expand.json", json.dumps(doc, separators=(",", ":")))
    if quota_stopped:
        # The phase is incomplete: leave the node unstamped (a later run resumes cache-first) and
        # exit 1 so the walker reads a quota stop, distinct from a data failure.
        raise QuotaFloorError(
            f"seats.aero quota floor {quota_floor} reached while expanding {slug!r}: "
            "wrote partial journeys, some legs not_run"
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
