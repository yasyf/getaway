"""Journey composition — the expand-node executor (``getaway expand run <slug>``).

Reads each leg intent's candidate pool by leg id — award legs from ``legs/<id>/shortlist.json``,
cash legs from ``legs/<id>/bridge.json`` quotes, an either leg from both — then walks the intents
in order building candidate chains where each candidate departs the prior candidate's dest (or the
intent's explicit override origins). An ``optional`` leg fans out to two variants — chains through
it and chains skipping it (a skipped leading positioning leg departs the trip's home origins
directly) — which compete on one Pareto front as ordinary journeys, their ids distinguishing them.
Two-leg plans compose exhaustively (bounded by shortlist budgets, byte-identical to HEAD's pairing
loop); a plan of three or more legs first drops any chain a cash boundary proves date-infeasible
before expansion, then cheap-ranks the rest on ``(miles, cash cents)`` and keeps the top
``plan.tuning.beam_width`` (default :data:`COMPOSE_BEAM_WIDTH`) **before** any ``/trips`` expansion
spends quota, disclosing the beam cut and the provably-infeasible count separately in the
``expand.json`` provenance. Only surviving chains expand their award legs (cache-first; a miss
spends quota through :class:`SeatsClient`).

Continuity refines with :func:`_structural_ok` on the expanded legs: a same-airport boundary
compares full local timestamps with the preference min-connection floor; a stay-marked boundary
bounds the gap to the declared nights; a cross-airport boundary compares dates only (seats.aero
clocks are naive local wall times — never do cross-airport clock math). Each surviving chain draws
fit facts and mandatory preference misses from :func:`fit.journey_fit`, then per-program cost
vectors and journey-level seat sufficiency. A journey with a *known* insufficient leg gates out;
``unknown`` stays visible with a verification warning. Round-trip outbounds with no bookable return
surface as a separate lead class, never as degenerate journeys; a three-or-more-leg plan whose full
chain never composes surfaces its longest composable prefix (up to a stay point) as a partial-chain
lead in ``expand.json``'s ``leads`` section, each remaining leg carrying its honest search state.

Pairing happens here, before ranking. Composition is deterministic CLI code — no agent prompts.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import click

from getaway import fit, prefs, trips
from getaway.constants import (
    DEFAULT_QUOTA_FLOOR,
    EXIT_AUTH,
    EXIT_NEGATIVE,
    NODE_TTL_HOURS,
    tuned,
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


def _leg_sweep_ages(slug: str, leg: dict, now: dt.datetime) -> tuple[str | None, float | None]:
    """The stalest of a leg's sweep artifacts (max-age): ``searched_at`` and cache age aggregated
    over every ``derive_specs``-driven sweep name the shortlist merges from. A bucketed or program-
    sweep leg writes only ``sweep-<label>.json`` (never the bare ``sweep.json``), so a single
    hardcoded name reads null and skips the stale-market TTL downgrade. A leg is only as fresh as
    its oldest component sweep."""
    from getaway.sweeps import derive_specs

    leg_id = leg["id"]
    stalest_at: str | None = None
    stalest_age: float | None = None
    for spec in derive_specs(leg):
        label = spec["label"]
        name = f"legs/{leg_id}/sweep.json" if label is None else f"legs/{leg_id}/sweep-{label}.json"
        prov = _sweep_provenance(slug, name)
        if prov is None:
            continue
        age = _cache_age_hours(prov["fetched_at"], now)
        if age is not None and (stalest_age is None or age > stalest_age):
            stalest_at, stalest_age = prov["fetched_at"], age
    return stalest_at, stalest_age


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


def _stay_nights_ok(prior_arr: str, nxt_dep: str, stay: dict) -> bool:
    """A stay-marked boundary: the whole-day gap between the prior arrival and the next departure
    lands within ``[min .. max]`` nights (date arithmetic — safe across timezones)."""
    nights = (dt.date.fromisoformat(nxt_dep[:10]) - dt.date.fromisoformat(prior_arr[:10])).days
    return stay["min"] <= nights <= stay["max"]


def _cross_airport_ok(prior_arr: str, nxt_dep: str) -> bool:
    """A cross-airport surface hop of unknown timing: the next leg departs no earlier than the prior
    leg's arrival DATE — seats.aero timestamps are naive local wall clocks, never subtracted across
    airports."""
    return dt.date.fromisoformat(nxt_dep[:10]) >= dt.date.fromisoformat(prior_arr[:10])


def _structural_ok(prior: dict, nxt: dict, prior_intent: dict, min_connection: int) -> bool:
    """Does ``nxt`` continue physically from ``prior``?

    A stay-marked boundary bounds the gap to ``[min .. max]`` nights (:func:`_stay_nights_ok`). A
    same-airport boundary compares full local timestamps with the connection floor (one airport, one
    clock). A cross-airport surface hop of unknown timing compares dates only
    (:func:`_cross_airport_ok`).
    """
    _, prior_dest, _, prior_arr = _leg_bounds(prior)
    nxt_origin, _, nxt_dep, _ = _leg_bounds(nxt)
    stay = prior_intent.get("stay_nights")
    if stay is not None:
        return _stay_nights_ok(prior_arr, nxt_dep, stay)
    if nxt_origin == prior_dest:
        gap = dt.datetime.fromisoformat(nxt_dep) - dt.datetime.fromisoformat(prior_arr)
        return gap >= dt.timedelta(minutes=min_connection)
    return _cross_airport_ok(prior_arr, nxt_dep)


def _chain_continuous(legs: list[dict], intents: list[dict], min_connection: int) -> bool:
    return all(
        _structural_ok(legs[i], legs[i + 1], intents[i], min_connection)
        for i in range(len(legs) - 1)
    )


def _continuity_reason(legs: list[dict], intents: list[dict], min_connection: int) -> str:
    """The first boundary where a manual chain fails to physically chain — its honest rejection."""
    for i in range(len(legs) - 1):
        if not _structural_ok(legs[i], legs[i + 1], intents[i], min_connection):
            return (
                f"leg {intents[i + 1]['id']!r} does not continue from leg {intents[i]['id']!r} "
                "(airports or timing do not chain)"
            )
    return "legs do not chain continuously"


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


def _leg_variants(legs: list[dict]) -> list[list[int]]:
    """Every active-leg index subset: mandatory legs always present, each ``optional`` leg both
    included and skipped, source order preserved. A plan with no optional legs yields exactly the
    full sequence (bit-identical to a single-variant compose); the all-optional-skipped empty
    variant is dropped (no zero-leg journey), as is any variant whose first leg targets ``$origins``
    — a fly-home-from-home shape is structurally meaningless (doc 49100ad), same class as empty."""
    variants: list[list[int]] = [[]]
    for i, leg in enumerate(legs):
        if leg.get("optional"):
            variants = [[*v, i] for v in variants] + [list(v) for v in variants]
        else:
            variants = [[*v, i] for v in variants]
    return [v for v in variants if v and not _is_return_intent(legs[v[0]])]


def _build_chains(
    legs: list[dict], pools: list[list[dict]], first_origins: list[str] | None = None
) -> list[list[dict]]:
    """Every candidate chain over the intents in order: a candidate extends a partial chain iff it
    departs the prior candidate's dest — or, when the intent declares explicit override origins, one
    of those (an open jaw departs where the chain didn't land). Airport anchoring only; timing is a
    :func:`_structural_ok` refinement on the expanded clocks. Shortlist rows carry a departure date
    but no arrival, so a departure-date prefilter would wrongly drop a dateline-crossing pairing
    whose arrival precedes its own departure date — the topology superset never gates on time.

    ``first_origins`` constrains the opening leg's departure airports: a variant that skips a
    leading optional leg (positioning) departs from the trip's home origins directly, so its new
    opening leg keeps only candidates leaving one of those airports. ``None`` leaves the opening leg
    unconstrained — the sweep already pinned it to the declared/home origins."""
    if first_origins is None:
        chains: list[list[dict]] = [[cand] for cand in pools[0]]
    else:
        allowed = set(first_origins)
        chains = [[cand] for cand in pools[0] if cand["origin"] in allowed]
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


def _pool_boundary_date_infeasible(prior: dict, nxt: dict, prior_intent: dict) -> bool:
    """Is this pool-candidate boundary PROVABLY date-infeasible before any ``/trips`` expansion?

    Only a boundary whose PRIOR leg is cash is provable: the quote carries ``arrives_local``, so its
    arrival date is known, and the check reuses :func:`_structural_ok`'s exact date-level predicates
    fed that arrival and the next leg's pre-expansion departure date. An award-prior boundary defers
    (a shortlist candidate has no arrival date until expanded), as does a same-airport boundary (a
    timestamp floor, not a date comparison) — both fall to :func:`_structural_ok` on the expanded
    clocks.
    """
    if prior["kind"] != "cash":
        return False
    prior_arr = prior["quote"]["arrives_local"]
    # A cash next-candidate judges by its quote's own clock — the value _structural_ok reads
    # post-expansion; an award candidate's shortlist date IS its expanded departure date.
    nxt_dep = nxt["quote"]["departs_local"] if nxt["kind"] == "cash" else nxt["date"]
    stay = prior_intent.get("stay_nights")
    if stay is not None:
        return not _stay_nights_ok(prior_arr, nxt_dep, stay)
    if nxt["origin"] == prior["dest"]:
        return False
    return not _cross_airport_ok(prior_arr, nxt_dep)


def _chain_date_feasible(chain: list[dict], variant_legs: list[dict]) -> bool:
    """No boundary in a pool-candidate chain is provably date-infeasible pre-expansion
    (:func:`_pool_boundary_date_infeasible`) — the pre-beam superset over :func:`_structural_ok`."""
    return not any(
        _pool_boundary_date_infeasible(chain[i], chain[i + 1], variant_legs[i])
        for i in range(len(chain) - 1)
    )


def _variant_trip(trip: dict, legs: list[dict], variant_legs: list[dict]) -> dict:
    """A trip view whose plan legs are this variant's — the return-side fit gate stays plan-derived
    (:func:`trips._targets_origins`) yet reads the variant's actual last intent, so a variant that
    skips the homeward leg is scored as the shorter shape it is. The full (no-skip) variant reuses
    the trip object untouched, keeping compose bit-identical when no optional leg fires."""
    if variant_legs is legs:
        return trip
    return {**trip, "plan": {**trip["plan"], "legs": variant_legs}}


def _variant_first_origins(
    legs: list[dict], variant_legs: list[dict], indices: list[int]
) -> list[str] | None:
    """The origin filter for a variant's opening leg. The plan's first leg is already sweep-pinned
    to its declared/home origins, so it stays unconstrained. A variant that skips a leading optional
    (positioning) departs the trip's home origins directly — unless its new opening leg declares its
    own explicit origins, which REPLACE the home filter (uniform open-jaw precedence, R-A)."""
    if indices[0] == 0:
        return None
    new_first = variant_legs[0]
    if new_first.get("origins"):
        return list(new_first["origins"])
    return legs[0]["origins"]


def _manual_chain_ref(intents: list[dict], chain: list[dict]) -> list[dict]:
    """A manual chain's declared ``{leg_id, candidate}`` references, reconstructed from its resolved
    pool candidates — the disclosure key when a manual chain is rejected after resolution."""
    ref: list[dict] = []
    for intent, cand in zip(intents, chain):
        if cand["kind"] == "award":
            ref.append({"leg_id": intent["id"], "candidate": cand["cand"]["id"]})
        else:
            ref.append(
                {
                    "leg_id": intent["id"],
                    "candidate": {
                        "gateway": cand["origin"],
                        "onward_dest": cand["dest"],
                        "date": cand["date"],
                    },
                }
            )
    return ref


def _compose_chains(
    trip: dict,
    prefs_doc: dict,
    legs: list[dict],
    tagged: list[tuple[list[dict], list[dict]]],
    expander: _Expander,
    avoid_transit: set[str],
    min_connection: int,
    leg_states: dict,
    now: Callable[[], dt.datetime],
    manual_rejected: list[dict] | None = None,
) -> tuple[list[dict], list[dict], set[str], bool, dict[str, dict[str, int]]]:
    """Expand beam-survivor chains lazily, refine continuity, gate transit/seats, emit journeys.

    Each ``tagged`` entry is ``(variant_legs, chain)`` — the chain's own intent subset, since an
    optional-leg skip drops a leg from the plan. Continuity, transit, shape, and fit all read the
    variant's legs, not the whole plan.

    ``manual_rejected`` present marks the manual-chain pass: emitted journeys carry ``provenance:
    "manual"``, and a chain that composition would silently drop (a leg with no bookable itinerary,
    or a continuity break) is disclosed there with its reason rather than dropped.

    Returns ``(journeys, gated, composed_heads, quota_stopped, variant_stats)``. ``composed_heads``
    is the set of first-leg award candidate ids that reached a journey — used to surface the rest as
    leads. ``variant_stats`` (R-M) maps each variant key — its included leg ids joined by ``+`` — to
    honest per-variant counts (``chains_built``, ``chains_expanded``, ``dropped_continuity``,
    ``journeys``), so an optional-leg variant whose chains all die at continuity is disclosed rather
    than silently starved; the caller attaches it to provenance only when the plan has optionals.
    """
    journeys: list[dict] = []
    gated: list[dict] = []
    composed_heads: set[str] = set()
    variant_stats: dict[str, dict[str, int]] = {}
    for variant_legs, chain in tagged:
        stats = variant_stats.setdefault(
            "+".join(leg["id"] for leg in variant_legs),
            {"chains_built": 0, "chains_expanded": 0, "dropped_continuity": 0, "journeys": 0},
        )
        stats["chains_built"] += 1
        legs_typed: list[dict] = []
        quota_stopped = False
        fail_reason = ""
        for intent, cand in zip(variant_legs, chain):
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
                fail_reason = f"leg {role!r} has no bookable {row['cabin']} itinerary"
                break
            leg_states[key] = {"state": "expanded"}
            legs_typed.append(_leg(role, row, detail, fetched_at))
        if quota_stopped:
            return journeys, gated, composed_heads, True, variant_stats
        if fail_reason:
            if manual_rejected is not None:
                ref = _manual_chain_ref(variant_legs, chain)
                manual_rejected.append({"chain": ref, "reason": fail_reason})
            continue
        stats["chains_expanded"] += 1
        if not _chain_continuous(legs_typed, variant_legs, min_connection):
            stats["dropped_continuity"] += 1
            if manual_rejected is not None:
                ref = _manual_chain_ref(variant_legs, chain)
                reason = _continuity_reason(legs_typed, variant_legs, min_connection)
                manual_rejected.append({"chain": ref, "reason": reason})
            continue
        jid = _journey_id(legs_typed)
        avoided = next(
            (code for code in _transit_points(legs_typed, variant_legs) if code in avoid_transit),
            None,
        )
        if avoided is not None:
            gated.append({"journey_id": jid, "reason": f"transits {avoided}, which you avoid"})
            continue
        variant = _variant_trip(trip, legs, variant_legs)
        fitted = fit.journey_fit(variant, prefs_doc, legs_typed, now)
        sufficiency = _seat_rollup(fitted["fit_facts"])
        if sufficiency == "insufficient":
            gated.append({"journey_id": jid, "reason": "a leg's live seats are below the party"})
            continue
        if chain[0]["kind"] == "award":
            composed_heads.add(chain[0]["cand"]["id"])
        journey = {
            "id": jid,
            "kind": _journey_shape(legs_typed, variant_legs),
            "legs": legs_typed,
            "fit_facts": fitted["fit_facts"],
            "preference_misses": fitted["preference_misses"],
            "cost": _cost(fitted["fit_facts"], legs_typed),
            "seat_sufficiency": sufficiency,
        }
        if manual_rejected is not None:
            journey["provenance"] = "manual"
        journeys.append(journey)
        stats["journeys"] += 1
    return journeys, gated, composed_heads, False, variant_stats


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


def _reaches_stay(legs: list[dict], prefix_len: int) -> bool:
    """Does a ``prefix_len``-leg prefix end where the traveller would stay? — its last intent marks
    a stay, or the very next intent flies home to ``$origins`` (the turnaround stop). A prefix that
    ends mid-flight is no lead: you cannot stop there."""
    return "stay_nights" in legs[prefix_len - 1] or _is_return_intent(legs[prefix_len])


def _lead_leg_summary(leg: dict) -> dict:
    """One concrete prefix leg, board-actionable — the per-leg shape :func:`_unpaired_leads` gives
    an outbound, generalized across cash and award."""
    origin, dest, _, _ = _leg_bounds(leg)
    summary = {"leg_id": leg["role"], "id": leg["id"], "cabin": leg["cabin"], "mode": leg["mode"]}
    if leg["mode"] == "cash":
        summary["origin"] = origin
        summary["dest"] = dest
        summary["amount_cents"] = leg["cash"]["amount_cents"]
    else:
        summary["dest"] = dest
        summary["source"] = leg["source"]
        summary["mileage"] = leg["detail"]["mileage"]
        summary["detail"] = leg["detail"]
    return summary


def _next_leg_states(leg: dict, reached: str, shortlist: dict | None, age: float | None) -> dict:
    """The immediately-next leg's honest search state, keyed exactly as the sweep wrote it (R-B): a
    ``$origins`` leg is origin-keyed at the airport the prefix reached; any other leg is dest-keyed
    over its own dests (per-dest entries when it has several), PLUS one entry per availability sweep
    LABEL its program sweeps wrote states under — additively, so a mixed buckets+program_sweeps leg
    keeps both its dest states and its region states. A searched-empty market whose sweep TTL has
    lapsed downgrades to ``unverified`` (R-C), mirroring :func:`_unpaired_leads`."""
    from getaway.sweeps import derive_specs

    states = shortlist["search_states"] if shortlist else {}
    expired = age is not None and age > _SWEEP_TTL_HOURS
    if _is_return_intent(leg):
        endpoints = [reached]
    else:
        endpoints = trips._leg_declared_dests(leg, [])
        for spec in derive_specs(leg):  # program sweeps key states by label, alongside dest states
            if spec["kind"] == "availability" and spec["label"] not in endpoints:
                endpoints.append(spec["label"])
    result: dict[str, dict] = {}
    for endpoint in endpoints:
        state = dict(states.get(endpoint, {"state": "not_run", "reason": "no_search"}))
        if state.get("state") == "searched_empty" and expired:
            state["verification"] = "unverified"
        result[endpoint] = state
    return result


def _remaining_states(
    slug: str,
    legs: list[dict],
    prefix_len: int,
    reached: str,
    shortlists: dict,
    now: dt.datetime,
) -> list[dict]:
    """Per-remaining-leg honest search state. The immediately-next leg reports its per-endpoint
    states (:func:`_next_leg_states`) — the markets that would continue the chain; every leg beyond
    it is unreachable until that one comes alive, so it reads ``not_run/prefix_incomplete``."""
    remaining: list[dict] = []
    for j in range(prefix_len, len(legs)):
        leg = legs[j]
        leg_id = leg["id"]
        searched_at, age = _leg_sweep_ages(slug, leg, now)
        if j == prefix_len:
            state: dict = _next_leg_states(leg, reached, shortlists.get(leg_id), age)
        else:
            state = {"state": "not_run", "reason": "prefix_incomplete"}
        remaining.append(
            {
                "leg_id": leg_id,
                "search_state": state,
                "searched_at": searched_at,
                "cache_age_hours": age,
            }
        )
    return remaining


def _partial_leads(
    slug: str,
    legs: list[dict],
    pools: list[list[dict]],
    shortlists: dict,
    expander: _Expander,
    leg_states: dict,
    min_connection: int,
    now: Callable[[], dt.datetime],
) -> tuple[list[dict], bool]:
    """The longest composable prefix reaching a stay point, surfaced as leads with each remaining
    leg's honest state — the ≥3-leg generalization of :func:`_unpaired_leads`. Runs only when no
    full chain composed. Returns ``(leads, quota_stopped)``; a quota floor crossed while expanding a
    prefix stops exactly like the main path (breaking candidate marked ``not_run/quota_floor``)."""
    now_dt = now()
    for k in range(len(legs) - 1, 0, -1):
        if not _reaches_stay(legs, k):
            continue
        leads: list[dict] = []
        seen: set[str] = set()
        for chain in _build_chains(legs[:k], pools[:k]):
            legs_typed: list[dict] = []
            failed = False
            for intent, cand in zip(legs[:k], chain):
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
                    return leads, True
                if detail is None:
                    leg_states[key] = {"state": "failed", "reason": "no_itinerary_in_cabin"}
                    failed = True
                    break
                leg_states.setdefault(key, {"state": "expanded"})
                legs_typed.append(_leg(role, row, detail, fetched_at))
            if failed or not _chain_continuous(legs_typed, legs[:k], min_connection):
                continue
            jid = _journey_id(legs_typed)
            if jid in seen:
                continue
            seen.add(jid)
            _, reached, _, _ = _leg_bounds(legs_typed[-1])
            leads.append(
                {
                    "prefix": [_lead_leg_summary(leg) for leg in legs_typed],
                    "reached": reached,
                    "remaining": _remaining_states(slug, legs, k, reached, shortlists, now_dt),
                }
            )
        if leads:
            leads.sort(key=lambda lead: sum(leg.get("mileage", 0) for leg in lead["prefix"]))
            return leads, False
    return [], False


def _manual_chains(slug: str) -> list | None:
    """The declared manual-chain artifact ``legs/manual.json`` (a list of candidate chains), or
    ``None`` when absent — the ``_ABSENT`` state that keeps a manual-free run byte-identical."""
    if "legs/manual.json" in trips.artifact_list(slug):
        return json.loads(trips.artifact_read(slug, "legs/manual.json"))
    return None


def _resolve_manual_candidate(candidate: str | dict, pool: list[dict]) -> tuple[dict | None, str]:
    """Match a manual entry's candidate to its leg's runtime pool. An award availability id or a
    cash ``(gateway, onward_dest, date)`` key must resolve to exactly one pool candidate; a
    reference that has aged out of the shortlist/bridge since the manual write (or is cabin-
    ambiguous) is a runtime miss — disclosed rather than silently dropped."""
    if isinstance(candidate, str):
        matches = [c for c in pool if c["kind"] == "award" and c["cand"]["id"] == candidate]
        subject = f"award candidate {candidate!r}"
    else:
        key = (candidate["gateway"], candidate["onward_dest"], candidate["date"])
        matches = [
            c for c in pool if c["kind"] == "cash" and (c["origin"], c["dest"], c["date"]) == key
        ]
        subject = f"cash quote {key}"
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return None, f"{subject} is no longer available"
    return None, f"{subject} is ambiguous across {len(matches)} candidates"


def _manual_coverage(
    legs: list[dict],
    chain: list[dict],
    leg_index: dict[str, int],
    plan_ids: list[str],
    mandatory_ids: list[str],
) -> tuple[list[int], str]:
    """Re-validate a declared chain against the CURRENT plan and resolve its VARIANT.
    ``manual.json`` is an input artifact that never fingerprint-invalidates, so ``set_patch`` may
    have changed ``plan.legs`` under it. Returns ``(positions, reason)``: ``reason`` is ``""`` iff
    the chain covers every mandatory leg once in plan order — optional legs freely included (in
    order) or skipped — and does not open on the homeward ``$origins`` leg (R-D); ``positions``
    are then the covered plan indices in declared order, threaded through composition like a
    :func:`_leg_variants` variant. Any
    mismatch is an honest ``manual_rejected`` reason, not a truncated pass or a raw ``KeyError``. A
    no-optional plan collapses to the every-leg rule, byte-identical to HEAD."""
    for entry in chain:
        if entry["leg_id"] not in leg_index:
            return [], f"leg {entry['leg_id']!r} is not a plan leg"
    ids = [entry["leg_id"] for entry in chain]
    positions, kind = trips._manual_chain_variant(legs, ids)
    if kind in ("order", "missing"):
        if len(mandatory_ids) == len(plan_ids):
            return positions, f"chain must cover every plan leg once in order {plan_ids}, got {ids}"
        if kind == "order":
            return positions, (
                f"chain legs must be a subsequence of plan order {plan_ids}, got {ids}"
            )
        missing = [m for m in mandatory_ids if m not in ids]
        return positions, (
            f"chain must cover every mandatory leg {mandatory_ids} once in plan order, "
            f"missing {missing}, got {ids}"
        )
    if kind == "home":
        return positions, (
            f"chain opens on the homeward leg {ids[0]!r} ({trips.ORIGINS_MARKER}); "
            "a manual chain must start with a real departure"
        )
    return positions, ""


def _manual_tagged(
    legs: list[dict], pools: list[list[dict]], manual_doc: list, manual_rejected: list[dict]
) -> list[tuple[list[dict], list[dict]]]:
    """Resolve each declared manual chain to a chain of runtime pool candidates, tagged with the
    chain's VARIANT legs (its covered subset — a manual chain covers every mandatory leg and may
    skip optionals, so the variant flows through composition as a :func:`_leg_variants` variant; a
    full-coverage chain reuses the plan object, keeping it byte-identical to HEAD). A chain that no
    longer covers the current plan, opens on the homeward leg, or has any unresolvable candidate is
    disclosed in ``manual_rejected`` and never tagged; the rest join composition as ordinary
    ``(variant_legs, chain)`` entries, priced through the same path as composed chains."""
    leg_index = {leg["id"]: i for i, leg in enumerate(legs)}
    plan_ids = [leg["id"] for leg in legs]
    mandatory_ids = [leg["id"] for leg in legs if not leg.get("optional")]
    full = list(range(len(legs)))
    tagged: list[tuple[list[dict], list[dict]]] = []
    for chain in manual_doc:
        positions, coverage = _manual_coverage(legs, chain, leg_index, plan_ids, mandatory_ids)
        if coverage:
            manual_rejected.append({"chain": chain, "reason": coverage})
            continue
        resolved: list[dict] = []
        reason = ""
        for entry in chain:
            cand, why = _resolve_manual_candidate(
                entry["candidate"], pools[leg_index[entry["leg_id"]]]
            )
            if cand is None:
                reason = f"leg {entry['leg_id']!r}: {why}"
                break
            resolved.append(cand)
        if reason:
            manual_rejected.append({"chain": chain, "reason": reason})
        else:
            variant_legs = legs if positions == full else [legs[p] for p in positions]
            tagged.append((variant_legs, resolved))
    return tagged


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

    # Optional legs fan out to include/skip variants sharing one cheap-rank front; no optional leg ⇒
    # a single full variant (HEAD-identical).
    full = list(range(len(legs)))
    tagged: list[tuple[list[dict], list[dict]]] = []
    for indices in _leg_variants(legs):
        variant_legs = legs if indices == full else [legs[i] for i in indices]
        variant_pools = pools if indices == full else [pools[i] for i in indices]
        first_origins = _variant_first_origins(legs, variant_legs, indices)
        for chain in _build_chains(variant_legs, variant_pools, first_origins):
            tagged.append((variant_legs, chain))
    tagged.sort(key=lambda t: (sum(c["miles"] for c in t[1]), sum(c["cash_cents"] for c in t[1])))

    # Two-leg plans compose exhaustively (HEAD-identical); ≥3-leg plans drop date-infeasible chains
    # (R-J) before beaming — beam_cut counts only feasible chains cut.
    beam_cut = 0
    date_infeasible = 0
    if len(legs) >= 3:
        feasible = [t for t in tagged if _chain_date_feasible(t[1], t[0])]
        date_infeasible = len(tagged) - len(feasible)
        kept = feasible[: tuned(plan, "beam_width")]
        beam_cut = len(feasible) - len(kept)
    else:
        kept = tagged
    journeys, gated, composed_heads, quota_stopped, variant_stats = _compose_chains(
        trip, prefs_doc, legs, kept, expander, avoid_transit, min_connection, leg_states, now
    )

    # Agent-declared manual chains, priced through the same path; dedup by id, absent ⇒ inert.
    manual_rejected: list[dict] = []
    if not quota_stopped:
        manual_doc = _manual_chains(slug)
        if manual_doc is not None:
            tagged_manual = _manual_tagged(legs, pools, manual_doc, manual_rejected)
            m_journeys, m_gated, m_heads, m_quota, _ = _compose_chains(
                trip, prefs_doc, legs, tagged_manual, expander, avoid_transit,
                min_connection, leg_states, now, manual_rejected=manual_rejected,
            )
            seen_ids = {j["id"] for j in journeys}
            for journey in m_journeys:
                if journey["id"] not in seen_ids:
                    journeys.append(journey)
                    seen_ids.add(journey["id"])
            seen_gated = {g["journey_id"] for g in gated}
            for g in m_gated:
                if g["journey_id"] not in seen_gated:
                    gated.append(g)
                    seen_gated.add(g["journey_id"])
            composed_heads |= m_heads
            quota_stopped = quota_stopped or m_quota

    # Round-trip unpaired outbounds; beam-cut ≥3-leg chains are truncation, not leads.
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

    # A ≥3-leg plan with no full chain surfaces its longest composable prefix; any journey ⇒ none.
    partial_leads: list[dict] = []
    if len(legs) >= 3 and not journeys and not quota_stopped:
        partial_leads, lead_quota_stopped = _partial_leads(
            slug, legs, pools, shortlists, expander, leg_states, min_connection, now
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
    if date_infeasible:
        provenance["date_infeasible"] = date_infeasible
    if any(leg.get("optional") for leg in legs):  # R-M: variants disclosed only when they exist
        provenance["variants"] = variant_stats
    doc = {
        "journeys": journeys,
        "unpaired_outbounds": unpaired,
        "gated": gated,
        "search_states": search_states,
        "leg_states": leg_states,
        "provenance": provenance,
    }
    if partial_leads:
        doc["leads"] = partial_leads
    if manual_rejected:
        doc["manual_rejected"] = manual_rejected
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
