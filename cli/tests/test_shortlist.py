import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import api_row, seed, shortlist_doc, sweep_envelope

from getaway import prefs, shortlist, trips
from getaway.store import NoData

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
DESTS = ["NRT", "HND", "BKK", "ICN", "SIN", "KIX", "OKA"]
WINDOW = {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def set_plan(slug: str, *, legs: list | None = None, **outbound_extra: object) -> None:
    """Default single outbound award leg (asia bucket); ``outbound_extra`` overrides its fields,
    or pass ``legs`` for a multi-intent plan."""
    if legs is None:
        outbound = {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": DESTS}]}
        outbound.update(outbound_extra)
        legs = [outbound]
    trips.set_patch(slug, {"plan": {"legs": legs}})


@pytest.fixture
def base(getaway_home: Path) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, {"cabin": "business", "party": 1, "window": WINDOW})
    set_plan(SLUG)
    return SLUG


def biz(mileage: str, seats: int = 2, airlines: str = "UA", direct: bool = True) -> dict:
    return {"J": {"mileage": mileage, "seats": seats, "airlines": airlines, "direct": direct}}


def _artifact_name(key: str) -> str:
    leg_id, _, label = key.partition(":")
    leaf = "sweep.json" if not label else f"sweep-{label}.json"
    return f"legs/{leg_id}/{leaf}"


def seed_leg(
    slug: str,
    key: str,
    rows: list[dict],
    *,
    kind: str = "search",
    expanded_origins: list[str] | None = None,
    states: dict | None = None,
    superseded_rows: dict | None = None,
) -> None:
    seed(slug, key, kind, rows, clock())
    env = sweep_envelope(
        rows,
        expanded_origins=expanded_origins,
        search_states=states or {},
        superseded_rows=superseded_rows,
    )
    trips.artifact_write(slug, _artifact_name(key), json.dumps(env))


def run(slug: str, rows: list[dict], key: str = "outbound:asia") -> dict:
    seed_leg(slug, key, rows)
    return shortlist.shortlist(slug, now=clock())


def ids(doc: dict) -> list[str]:
    return [c["id"] for c in doc["candidates"]]


# --- all cabins as (id, cabin) candidates ---


def test_all_available_cabins_become_candidates(base: str) -> None:
    row = api_row(
        "R",
        "SFO",
        "NRT",
        "2026-09-05",
        "united",
        {"J": {"mileage": "80000", "seats": 2}, "Y": {"mileage": "30000", "seats": 4}},
    )
    doc = run(base, [row])
    by_cabin = {c["cabin"]: c for c in doc["candidates"]}
    assert set(by_cabin) == {"J", "Y"}  # cabin is no longer a hard filter
    assert by_cabin["J"]["mileage"] == 80000
    assert by_cabin["Y"]["mileage"] == 30000


def test_teaser_seats_do_not_gate(base: str) -> None:
    trips.set_patch(base, {"party": 2})
    row = api_row("R", "SFO", "NRT", "2026-09-05", "united", biz("80000", seats=1))
    doc = run(base, [row])
    assert ids(doc) == ["R"]  # sufficiency is judged on expanded rows at rank time, not here


def test_high_mileage_not_gated(base: str) -> None:
    row = api_row("R", "SFO", "NRT", "2026-09-05", "united", biz("500000"))
    doc = run(base, [row])
    assert ids(doc) == ["R"]  # mileage softens to a fit fact; no ceiling filter


# --- feasibility / pseudo-codes ---


def test_origin_outside_plan_dropped(base: str) -> None:
    set_plan(base, program_sweeps=[{"source": "united", "dest_region": "Asia"}])
    seed_leg(base, "outbound:asia", [], kind="search", expanded_origins=["SFO"])
    seed_leg(
        base,
        "outbound:united-asia",
        [
            api_row("HOME", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
            api_row("FOREIGN", "FRA", "NRT", "2026-09-06", "united", biz("70000")),
        ],
        kind="availability",
    )
    doc = shortlist.shortlist(base, now=clock())
    assert ids(doc) == ["HOME"]


def test_pseudo_code_origin_filters_on_observed_expansion(base: str) -> None:
    # QAF is a server-expanded pseudo-code with no registry airport list; feasibility must compare
    # the concrete airports the sweep recorded, never the QAF literal, and must not raise.
    set_plan(base, origins=["QAF"], program_sweeps=[{"source": "united", "dest_region": "Africa"}])
    seed_leg(base, "outbound:asia", [], kind="search", expanded_origins=["CMN", "CAI"])
    seed_leg(
        base,
        "outbound:united-africa",
        [
            api_row("KEEP", "CMN", "NRT", "2026-09-05", "united", biz("80000")),
            api_row("DROP", "FRA", "NRT", "2026-09-06", "united", biz("70000")),
        ],
        kind="availability",
    )
    doc = shortlist.shortlist(base, now=clock())
    assert ids(doc) == ["KEEP"]


# --- expansion budgets ---


def test_per_endpoint_budget_truncates_and_discloses(base: str) -> None:
    rows = [
        api_row(f"R{i}", "SFO", "NRT", f"2026-09-{5 + i:02d}", "united", biz(str(80000 + i)))
        for i in range(15)
    ]
    doc = run(base, rows)
    assert len(doc["candidates"]) == 12  # EXPANSION_BUDGET_PER_ENDPOINT
    assert doc["truncation"]["NRT"] == {"considered": 15, "kept": 12}


@pytest.mark.parametrize("budget", [5, 14])
def test_per_endpoint_budget_honors_tuning_override(base: str, budget: int) -> None:
    trips.set_patch(
        base,
        {
            "plan": {
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": DESTS}]}],
                "tuning": {"expansion_budget_per_endpoint": budget},
            }
        },
    )
    rows = [
        api_row(f"R{i}", "SFO", "NRT", f"2026-09-{5 + i:02d}", "united", biz(str(80000 + i)))
        for i in range(15)
    ]
    doc = run(base, rows)
    assert len(doc["candidates"]) == budget  # the tuned budget replaces the default 12
    assert doc["truncation"]["NRT"] == {"considered": 15, "kept": budget}


def test_budget_is_per_endpoint_not_global(base: str) -> None:
    rows = []
    for dest in ("NRT", "BKK"):
        for i in range(15):
            date = f"2026-09-{5 + i:02d}"
            rows.append(api_row(f"{dest}{i}", "SFO", dest, date, "united", biz(str(80000 + i))))
    doc = run(base, rows)
    kept = {}
    for cand in doc["candidates"]:
        kept[cand["dest"]] = kept.get(cand["dest"], 0) + 1
    assert kept == {"NRT": 12, "BKK": 12}  # one hot endpoint cannot starve the other


def test_home_origin_floor_survives_endpoint_budget(base: str) -> None:
    prefs.set_patch({"home_airport": "SFO"})
    set_plan(base, origins=["WST"])
    rows = [
        api_row(f"LAX{i}", "LAX", "NRT", f"2026-09-{5 + i:02d}", "united", biz(str(80000 + i)))
        for i in range(12)
    ]
    rows.append(api_row("HOME", "SFO", "NRT", "2026-09-30", "united", biz("200000")))

    doc = run(base, rows)

    assert ids(doc) == [*[f"LAX{i}" for i in range(11)], "HOME"]
    assert doc["truncation"]["NRT"] == {
        "considered": 13,
        "kept": 12,
        "displaced": 1,
    }


def test_no_floor_candidates_preserves_cohort_selection(base: str) -> None:
    set_plan(base, origins=["QAF"])
    observed_origins = [f"O{i:02d}" for i in range(12)]
    rows = [
        api_row(f"CMN{i:02d}", origin, "NRT", "2026-09-05", "united", biz("80000"))
        for i, origin in enumerate(observed_origins)
    ]
    rows.append(api_row("PSEUDO", "QAF", "NRT", "2026-09-06", "united", biz("80000")))
    seed_leg(base, "outbound:asia", rows, expanded_origins=observed_origins)

    doc = shortlist.shortlist(base, now=clock())

    assert ids(doc) == ["CMN00", "PSEUDO", *[f"CMN{i:02d}" for i in range(1, 11)]]
    assert doc["truncation"] == {"NRT": {"considered": 13, "kept": 12}}


@pytest.mark.parametrize(
    ("region_origin", "expanded_origin", "same_cohort_origin"),
    [("MEX", "CUN", "GDL"), ("SEA", "SIN", "KUL")],
    ids=["mex-region-code", "sea-region-code"],
)
def test_declared_region_origin_is_not_floored_without_home_airport(
    base: str, region_origin: str, expanded_origin: str, same_cohort_origin: str
) -> None:
    set_plan(base, origins=[region_origin])
    rows = [
        api_row(
            f"COHORT-{i:02d}",
            expanded_origin,
            "NRT",
            f"2026-09-{5 + i:02d}",
            "united",
            biz("80000"),
        )
        for i in range(12)
    ]
    rows.extend(
        [
            api_row(
                "SAME-COHORT",
                same_cohort_origin,
                "NRT",
                "2026-09-05",
                "united",
                biz("80000"),
            ),
            api_row(
                "DECLARED",
                region_origin,
                "NRT",
                "2026-09-30",
                "united",
                biz("80000"),
            ),
        ]
    )

    doc = run(base, rows)

    assert ids(doc) == [f"COHORT-{i:02d}" for i in range(12)]
    assert doc["truncation"] == {"NRT": {"considered": 14, "kept": 12}}


def test_floor_pick_already_in_round_robin_dedupes_without_spending_twice(base: str) -> None:
    prefs.set_patch({"home_airport": "SFO"})
    rows = [
        api_row(f"R{i}", "SFO", "NRT", f"2026-09-{5 + i:02d}", "united", biz(str(80000 + i)))
        for i in range(13)
    ]

    doc = run(base, rows)

    assert ids(doc) == [f"R{i}" for i in range(12)]
    assert doc["truncation"] == {"NRT": {"considered": 13, "kept": 12}}


def test_floor_origin_displacement_is_disclosed(base: str) -> None:
    prefs.set_patch({"home_airport": "LAX"})
    set_plan(base, origins=["SFO", "LAX"])
    rows = [
        api_row(f"SFO{i}", "SFO", "NRT", f"2026-09-{5 + i:02d}", "united", biz(str(80000 + i)))
        for i in range(12)
    ]
    rows.append(api_row("LAX-FLOOR", "LAX", "NRT", "2026-09-30", "united", biz("200000")))

    doc = run(base, rows)

    assert ids(doc) == [*[f"SFO{i}" for i in range(11)], "LAX-FLOOR"]
    assert doc["truncation"] == {
        "NRT": {"considered": 13, "kept": 12, "displaced": 1}
    }


# --- veto, avoid airlines, search-state passthrough ---


def test_dest_veto_drops_final_destination(base: str) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    doc = run(
        base,
        [
            api_row("KEEP", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
            api_row("VETO", "SFO", "ICN", "2026-09-06", "united", biz("70000")),
        ],
    )
    assert ids(doc) == ["KEEP"]


def test_hard_avoid_drops_only_when_every_airline_avoided(base: str) -> None:
    prefs.set_patch({"avoid_airlines": [{"code": "AA", "name": "American", "strength": "hard"}]})
    doc = run(
        base,
        [
            api_row("ALL", "SFO", "NRT", "2026-09-05", "united", biz("80000", airlines="AA")),
            api_row("MIX", "SFO", "HND", "2026-09-06", "united", biz("81000", airlines="AA, UA")),
        ],
    )
    assert set(ids(doc)) == {"MIX"}


def test_soft_avoid_sorts_never_filters(base: str) -> None:
    prefs.set_patch({"avoid_airlines": [{"code": "NH", "name": "ANA", "strength": "soft"}]})
    doc = run(
        base,
        [
            api_row("SOFT", "SFO", "NRT", "2026-09-05", "united", biz("80000", airlines="NH")),
            api_row("CLEAN", "SFO", "HND", "2026-09-06", "united", biz("90000", airlines="UA")),
        ],
    )
    assert ids(doc) == ["CLEAN", "SOFT"]  # soft-avoid cheaper row kept, ranked below the clean one
    assert {c["id"]: c["soft"] for c in doc["candidates"]} == {"CLEAN": False, "SOFT": True}


def cohort_cand(
    cid: str, mileage: int, soft: bool, cabin: str = "J", date: str = "2026-09-05"
) -> dict:
    return {
        "id": cid,
        "mileage": mileage,
        "soft": soft,
        "cabin": cabin,
        "date": date,
        "source": "united",
    }


def test_cohort_select_keeps_cheaper_soft_over_pricier_clean_under_budget() -> None:
    # Soft-avoid rides ranking, never retrieval: when the per-endpoint budget truncates the
    # cohort to one, the cheaper soft-avoided candidate survives over the pricier clean one.
    cands = [cohort_cand("CLEAN", 90000, soft=False), cohort_cand("SOFT", 70000, soft=True)]
    selected = shortlist._cohort_select(cands, budget=1, window_dates={"2026-09-05"})
    assert [c["id"] for c in selected] == ["SOFT"]


def test_cohort_select_interleaves_cabins_so_business_survives_economy_flood(base: str) -> None:
    # Driver's live regression (R-I): >=budget economy rows across distinct dates fill the budget
    # and drop every same-route business row unless the round-robin interleaves cabin cost tiers.
    econ = {"Y": {"mileage": "12500"}}
    rows = [
        api_row(f"Y{i}", "SFO", "NRT", f"2026-09-{5 + i:02d}", "united", econ) for i in range(12)
    ]
    rows.extend(
        api_row(f"J{i}", "SFO", "NRT", f"2026-09-{5 + i:02d}", "united", biz("40000"))
        for i in range(6)
    )

    doc = run(base, rows)

    by_cabin: dict[str, int] = {}
    for cand in doc["candidates"]:
        by_cabin[cand["cabin"]] = by_cabin.get(cand["cabin"], 0) + 1
    assert by_cabin == {"Y": 6, "J": 6}  # every cost tier represented, not an all-economy flood
    assert len(doc["candidates"]) == 12  # EXPANSION_BUDGET_PER_ENDPOINT
    assert doc["truncation"]["NRT"] == {"considered": 18, "kept": 12}


def test_cohort_select_interleaves_window_membership_over_padding() -> None:
    # R-L: out-of-window padding is the cheapest per-cabin cohort and would fill the budget alone;
    # window membership, the outermost axis, keeps in-window rows too.
    pad = [
        cohort_cand(f"PAD{i}", 40000, soft=False, date=f"2026-08-{26 + i:02d}") for i in range(6)
    ]
    inw = [cohort_cand(f"IN{i}", 41000, soft=False, date=f"2026-09-{6 + i:02d}") for i in range(6)]
    window = {f"2026-09-{6 + i:02d}" for i in range(6)}

    selected = shortlist._cohort_select(pad + inw, budget=6, window_dates=window)

    assert sum(c["id"].startswith("IN") for c in selected) == 3  # in-window rows survive
    assert sum(c["id"].startswith("PAD") for c in selected) == 3  # padding stays represented
    # Reinject: strip the axis (all dates in-window) and padding floods the budget.
    all_in = window | {f"2026-08-{26 + i:02d}" for i in range(6)}
    flooded = shortlist._cohort_select(pad + inw, budget=6, window_dates=all_in)
    assert [c["id"] for c in flooded] == [f"PAD{i}" for i in range(6)]


def test_cohort_select_single_window_side_selects_byte_identically() -> None:
    # Degeneracy pin (R-L): a single-sided pool selects identically judged in- or out-of-window.
    cands = [
        cohort_cand(f"R{i}", 80000 + i, soft=False, date=f"2026-09-{5 + i:02d}") for i in range(4)
    ]
    dates = {f"2026-09-{5 + i:02d}" for i in range(4)}
    all_in = shortlist._cohort_select(list(cands), budget=3, window_dates=dates)
    all_out = shortlist._cohort_select(list(cands), budget=3, window_dates=set())
    assert [c["id"] for c in all_in] == [c["id"] for c in all_out] == ["R0", "R1", "R2"]


def test_window_membership_survives_endpoint_budget_end_to_end(base: str) -> None:
    # R-L end to end: the trip window (via _window_dates) drives the axis through the shortlist.
    trips.set_patch(
        base,
        {
            "plan": {
                "legs": [{"origins": ["SFO"], "buckets": [{"name": "asia", "dests": DESTS}]}],
                "tuning": {"expansion_budget_per_endpoint": 6},
            }
        },
    )
    rows = [
        api_row(f"PAD{i}", "SFO", "NRT", f"2026-08-{26 + i:02d}", "united", biz("40000"))
        for i in range(6)
    ]
    rows.extend(
        api_row(f"IN{i}", "SFO", "NRT", f"2026-09-{6 + i:02d}", "united", biz("41000"))
        for i in range(6)
    )

    doc = run(base, rows)

    kept = ids(doc)
    assert sum(cid.startswith("IN") for cid in kept) == 3  # window rows survive the tuned budget
    assert sum(cid.startswith("PAD") for cid in kept) == 3  # padding still represented
    assert doc["truncation"]["NRT"] == {"considered": 12, "kept": 6}


def test_search_states_pass_through_from_sweep(base: str) -> None:
    states = {"NRT": {"state": "complete"}, "BKK": {"state": "searched_empty"}}
    seed_leg(
        base,
        "outbound:asia",
        [api_row("R", "SFO", "NRT", "2026-09-05", "united", biz("80000"))],
        states=states,
    )
    doc = shortlist.shortlist(base, now=clock())
    assert doc["search_states"] == states
    assert doc["leg"] == "outbound"
    assert "provenance" not in doc


def test_superseded_rows_sum_across_constituent_sweeps(base: str) -> None:
    set_plan(base, program_sweeps=[{"source": "united", "dest_region": "Asia"}])
    seed_leg(base, "outbound:asia", [], superseded_rows={"count": 2, "ids": ["A", "B"]})
    seed_leg(
        base,
        "outbound:united-asia",
        [],
        kind="availability",
        superseded_rows={"count": 3, "ids": ["C", "D", "E"]},
    )

    doc = shortlist.shortlist(base, now=clock())

    assert doc["provenance"] == {"superseded_rows": {"count": 5}}


# --- return leg ---


def _round_trip_legs() -> list[dict]:
    return [
        {"origins": ["SFO"], "buckets": [{"name": "asia", "dests": DESTS}]},
        {"id": "return", "dests": "$origins"},
    ]


def test_return_leg_shortlists_by_return_origin(base: str) -> None:
    set_plan(base, legs=_round_trip_legs())
    rows = [
        api_row("RET", "NRT", "SFO", "2026-09-20", "united", biz("70000")),
        api_row("RET2", "OKA", "SFO", "2026-09-21", "united", biz("75000")),
    ]
    seed_leg(base, "return", rows)
    doc = shortlist.shortlist(base, leg="return", now=clock())
    assert set(ids(doc)) == {"RET", "RET2"}
    assert doc["leg"] == "return"


def test_return_leg_home_dests_exempt_from_veto(base: str) -> None:
    # A leg flying to "$origins" partitions by origin and never applies the destination veto: home
    # is not a place to avoid, even when it collides with an avoid list.
    prefs.set_patch({"avoid_destinations": ["SFO"]})
    set_plan(base, legs=_round_trip_legs())
    seed_leg(base, "return", [api_row("RET", "NRT", "SFO", "2026-09-20", "united", biz("70000"))])
    doc = shortlist.shortlist(base, leg="return", now=clock())
    assert ids(doc) == ["RET"]


# --- onward (cash/either hop) ---


def gw(cid: str, dest: str, date: str, mileage: int) -> dict:
    return {
        "id": cid,
        "cabin": "J",
        "date": date,
        "origin": "SFO",
        "dest": dest,
        "source": "united",
        "mileage": mileage,
        "seats": 2,
        "airlines": "UA",
        "direct": True,
        "soft": False,
        "departure_day_match": False,
    }


def onward_row(rid: str, origin: str, dest: str, date: str, mileage: str) -> dict:
    return api_row(rid, origin, dest, date, "aeroplan", {"J": {"mileage": mileage, "seats": 2}})


def _hybrid_legs() -> list[dict]:
    return [
        {"origins": ["SFO"], "dests": ["NRT"]},  # outbound award to the gateway
        {"id": "hop", "mode": "either", "dests": ["OKA"]},  # either hop NRT -> OKA
        {"id": "return", "dests": "$origins"},
    ]


def _write_gateway_shortlist(base: str) -> None:
    trips.artifact_write(
        base,
        "legs/outbound/shortlist.json",
        json.dumps(
            {
                "candidates": [gw("GW", "NRT", "2026-09-12", 80000)],
                "considered": 1,
                "search_states": {},
                "leg": "outbound",
                "truncation": {},
            }
        ),
    )


def test_onward_drops_rows_before_earliest_gateway_arrival(base: str) -> None:
    set_plan(base, legs=_hybrid_legs())
    _write_gateway_shortlist(base)
    seed(
        base,
        "hop",
        "search",
        [
            onward_row("EARLY", "NRT", "OKA", "2026-09-10", "30000"),
            onward_row("LATER", "NRT", "OKA", "2026-09-13", "35000"),
        ],
        clock(),
    )
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert {m["id"] for m in doc["minima"]} == {"LATER"}  # EARLY departs before gateway arrival


def test_onward_bridge_pairs_cross_gateways_and_dests_without_award_gating(base: str) -> None:
    # The cash lane prices every reachable (gateway, onward_dest, arrival-date) pair — never gated
    # on award availability (the double-gating the chain-builder was contracted to undo).
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"]},
            {"id": "hop", "mode": "cash", "dests": ["OKA", "ISG"]},
            {"id": "return", "dests": "$origins"},
        ],
    )
    _write_gateway_shortlist(base)
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert doc["minima"] == []  # a pure-cash leg has no award option
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("NRT", "OKA", "2026-09-12"),
        ("NRT", "ISG", "2026-09-12"),
    }


def test_onward_stay_nights_shifts_bridge_pair_dates(base: str) -> None:
    # Arrival NRT 2026-09-12, stay {2,3} -> hop departs [arrival+2 .. arrival+3] = 09-14/09-15.
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"], "stay_nights": {"min": 2, "max": 3}},
            {"id": "hop", "mode": "cash", "dests": ["OKA"]},
            {"id": "return", "dests": "$origins"},
        ],
    )
    _write_gateway_shortlist(base)
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("NRT", "OKA", "2026-09-14"),
        ("NRT", "OKA", "2026-09-15"),
    }


def test_onward_no_stay_keeps_same_date_pairs(base: str) -> None:
    # Degeneracy: no stay marker leaves the hop's candidate date at the predecessor arrival.
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"]},
            {"id": "hop", "mode": "cash", "dests": ["OKA"]},
            {"id": "return", "dests": "$origins"},
        ],
    )
    _write_gateway_shortlist(base)
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert {p["date"] for p in doc["bridge_pairs"]} == {"2026-09-12"}


def test_onward_positioning_leg_prices_own_origins_over_window(base: str) -> None:
    # A leading cash leg (endpoint_source None, inputs []) prices its own origins over its window.
    set_plan(
        base,
        legs=[
            {
                "id": "pos",
                "mode": "cash",
                "origins": ["SFO"],
                "dests": ["LAX"],
                "window": {"start": "2026-09-01", "end": "2026-09-03"},
            },
            {"id": "onward", "dests": ["NRT"]},
        ],
    )
    doc = shortlist.onward_minima(base, "pos", now=clock())
    assert doc["minima"] == []
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("SFO", "LAX", "2026-09-01"),
        ("SFO", "LAX", "2026-09-02"),
        ("SFO", "LAX", "2026-09-03"),
    }


def test_onward_cash_after_cash_prices_carried_union(base: str) -> None:
    # A cash hop past another cash leg draws gateways from endpoint_source.union, not a shortlist.
    set_plan(
        base,
        legs=[
            {
                "id": "pos",
                "mode": "cash",
                "origins": ["SFO"],
                "dests": ["LAX"],
                "window": {"start": "2026-09-01", "end": "2026-09-02"},
            },
            {
                "id": "hop",
                "mode": "cash",
                "dests": ["SAN"],
                "window": {"start": "2026-09-03", "end": "2026-09-03"},
            },
            {"id": "return", "dests": "$origins"},
        ],
    )
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert doc["minima"] == []
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("LAX", "SAN", "2026-09-03"),
    }


def test_onward_optional_positioning_prices_home_gateway_via_skip(base: str) -> None:
    # R-A cash lane: the onward pairs node departs the positioning dest (LAX) AND home (SFO, the
    # boundary if the optional positioning leg is skipped) — skip transparency mirrors the sweep.
    set_plan(
        base,
        legs=[
            {
                "id": "pos",
                "mode": "cash",
                "origins": ["SFO"],
                "dests": ["LAX"],
                "optional": True,
                "window": {"start": "2026-09-01", "end": "2026-09-01"},
            },
            {
                "id": "onward",
                "mode": "cash",
                "dests": ["NRT"],
                "window": {"start": "2026-09-05", "end": "2026-09-05"},
            },
        ],
    )
    doc = shortlist.onward_minima(base, "onward", now=clock())
    assert {p["gateway"] for p in doc["bridge_pairs"]} == {"LAX", "SFO"}
    assert {(p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {("NRT", "2026-09-05")}


def test_onward_either_after_pure_cash_prices_carried_union(base: str) -> None:
    # Regression: an either hop past a pure-cash leg draws gateways from endpoint_source.union
    # (the cash leg's dests), never a raw KeyError from the missing origins fallback.
    set_plan(
        base,
        legs=[
            {
                "id": "pos",
                "mode": "cash",
                "origins": ["SFO"],
                "dests": ["LAX"],
                "window": {"start": "2026-09-01", "end": "2026-09-02"},
            },
            {
                "id": "hop",
                "mode": "either",
                "dests": ["SAN"],
                "window": {"start": "2026-09-03", "end": "2026-09-03"},
            },
            {"id": "return", "dests": "$origins"},
        ],
    )
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert doc["minima"] == []  # no hop award availability seeded
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("LAX", "SAN", "2026-09-03"),
    }


def test_onward_override_origins_replace_chained_gateways(base: str) -> None:
    # Open jaw: a cash hop's explicit origins REPLACE the chained gateways — the from-shortlist NRT
    # is dropped, the hop departs only KIX over its own window (mirrors the sweep lane).
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"]},
            {
                "id": "hop",
                "mode": "cash",
                "origins": ["KIX"],
                "dests": ["OKA"],
                "window": {"start": "2026-09-14", "end": "2026-09-15"},
            },
            {"id": "return", "dests": "$origins"},
        ],
    )
    _write_gateway_shortlist(base)  # outbound reaches NRT@2026-09-12
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert {p["gateway"] for p in doc["bridge_pairs"]} == {"KIX"}  # no NRT from the dropped chain
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("KIX", "OKA", "2026-09-14"),
        ("KIX", "OKA", "2026-09-15"),
    }


def test_onward_either_after_award_override_replaces_and_keeps_own_minima(base: str) -> None:
    # Regression (S2-fix-3): an either hop's explicit origins REPLACE the pure-award chain gateway —
    # bridge departs only KIX, and the hop's own KIX award row survives the origin filter.
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"]},
            {
                "id": "hop",
                "mode": "either",
                "origins": ["KIX"],
                "dests": ["OKA"],
                "window": {"start": "2026-09-14", "end": "2026-09-15"},
            },
            {"id": "return", "dests": "$origins"},
        ],
    )
    _write_gateway_shortlist(base)  # outbound reaches NRT@2026-09-12
    seed(
        base,
        "hop",
        "search",
        [onward_row("HOPKIX", "KIX", "OKA", "2026-09-14", "20000")],
        clock(),
    )
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("KIX", "OKA", "2026-09-14"),
        ("KIX", "OKA", "2026-09-15"),
    }  # exactly the override pairs — no NRT from the dropped chain
    assert {(m["gateway"], m["onward_dest"], m["date"]) for m in doc["minima"]} == {
        ("KIX", "OKA", "2026-09-14"),
    }  # the KIX award row kept; the origin filter keeps override-origin rows


def test_onward_either_after_either_carries_shortlist_and_union(base: str) -> None:
    # An either hop past an either predecessor prices its award-reached gateway (NRT@09-12) and its
    # cash-reachable union dest (NRT over the hop window): the cash lane ignores its own award lane.
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"], "mode": "either"},
            {
                "id": "hop",
                "mode": "either",
                "dests": ["OKA"],
                "window": {"start": "2026-09-14", "end": "2026-09-15"},
            },
            {"id": "return", "dests": "$origins"},
        ],
    )
    _write_gateway_shortlist(base)  # outbound (either) reaches NRT@2026-09-12
    doc = shortlist.onward_minima(base, "hop", now=clock())
    assert {(p["gateway"], p["onward_dest"], p["date"]) for p in doc["bridge_pairs"]} == {
        ("NRT", "OKA", "2026-09-12"),
        ("NRT", "OKA", "2026-09-14"),
        ("NRT", "OKA", "2026-09-15"),
    }


def test_onward_empty_chained_gateway_raises_nodata(base: str) -> None:
    # An empty predecessor shortlist leaves the cash hop no gateway: NoData, no HTTP.
    set_plan(
        base,
        legs=[
            {"origins": ["SFO"], "dests": ["NRT"]},
            {"id": "hop", "mode": "cash", "dests": ["OKA"]},
            {"id": "return", "dests": "$origins"},
        ],
    )
    trips.artifact_write(
        base, "legs/outbound/shortlist.json", json.dumps(shortlist_doc([], considered=0))
    )
    with pytest.raises(NoData):
        shortlist.onward_minima(base, "hop", now=clock())
