import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import api_row, seed, sweep_envelope

from getaway import prefs, shortlist, trips

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
DESTS = ["NRT", "HND", "BKK", "ICN", "SIN", "KIX", "OKA"]
WINDOW = {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def set_plan(slug: str, **extra: object) -> None:
    plan: dict = {"trip_type": "one_way", "origins": ["SFO"]}
    plan["buckets"] = [{"name": "asia", "dests": DESTS}]
    plan.update(extra)
    trips.set_patch(slug, {"plan": plan})


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
    if key == "return":
        return "legs/return/sweep.json"
    _, _, label = key.partition(":")
    return f"legs/outbound/sweep-{label}.json"


def seed_leg(
    slug: str,
    key: str,
    rows: list[dict],
    *,
    kind: str = "search",
    expanded_origins: list[str] | None = None,
    states: dict | None = None,
) -> None:
    seed(slug, key, kind, rows, clock())
    env = sweep_envelope(rows, expanded_origins=expanded_origins, search_states=states or {})
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


def cohort_cand(cid: str, mileage: int, soft: bool) -> dict:
    return {"id": cid, "mileage": mileage, "soft": soft, "date": "2026-09-05", "source": "united"}


def test_cohort_select_keeps_cheaper_soft_over_pricier_clean_under_budget() -> None:
    # Soft-avoid rides ranking, never retrieval: when the per-endpoint budget truncates the
    # cohort to one, the cheaper soft-avoided candidate survives over the pricier clean one.
    cands = [cohort_cand("CLEAN", 90000, soft=False), cohort_cand("SOFT", 70000, soft=True)]
    selected = shortlist._cohort_select(cands, budget=1)
    assert [c["id"] for c in selected] == ["SOFT"]


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


# --- return leg ---


def test_return_leg_shortlists_by_return_origin(base: str) -> None:
    set_plan(base, trip_type="round_trip")
    rows = [
        api_row("RET", "NRT", "SFO", "2026-09-20", "united", biz("70000")),
        api_row("RET2", "OKA", "SFO", "2026-09-21", "united", biz("75000")),
    ]
    seed_leg(base, "return", rows)
    doc = shortlist.shortlist(base, leg="return", now=clock())
    assert set(ids(doc)) == {"RET", "RET2"}
    assert doc["leg"] == "return"


# --- gateway mode ---


def test_gateway_mode_omits_dest_veto(base: str) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    set_plan(base, hybrid={"gateways": ["NRT", "ICN"], "onward_dests": ["OKA"], "max_hybrids": 3})
    seed_leg(
        base,
        "outbound:gateways",
        [
            api_row("G-NRT", "SFO", "NRT", "2026-09-05", "united", biz("80000")),
            api_row("G-ICN", "SFO", "ICN", "2026-09-06", "united", biz("70000")),
        ],
    )
    doc = shortlist.shortlist(base, gateway=True, now=clock())
    assert set(ids(doc)) == {"G-NRT", "G-ICN"}  # gateways are waypoints, veto omitted


# --- onward (hybrid) ---


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


def test_onward_drops_rows_before_earliest_gateway_arrival(base: str) -> None:
    set_plan(base, hybrid={"gateways": ["NRT"], "onward_dests": ["OKA"], "max_hybrids": 3})
    trips.artifact_write(
        base,
        "legs/outbound/shortlist-gateway.json",
        json.dumps(
            {
                "candidates": [gw("GW", "NRT", "2026-09-12", 80000)],
                "considered": 1,
                "search_states": {},
                "leg": "outbound:gateway",
                "truncation": {},
            }
        ),
    )
    seed(
        base,
        "outbound:onward",
        "search",
        [
            onward_row("EARLY", "NRT", "OKA", "2026-09-10", "30000"),
            onward_row("LATER", "NRT", "OKA", "2026-09-13", "35000"),
        ],
        clock(),
    )
    doc = shortlist.onward_minima(base, now=clock())
    assert {m["id"] for m in doc["minima"]} == {"LATER"}  # EARLY departs before gateway arrival
