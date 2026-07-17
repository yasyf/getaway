import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import expand_doc

from getaway import enhance, factors, prefs, trips

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def _new(getaway_home: Path, plan: dict) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(
        SLUG,
        {
            "cabin": "business",
            "party": 1,
            "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 10},
            "plan": plan,
        },
    )
    return SLUG


DIRECT_PLAN = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "return", "dests": "$origins"},
    ]
}

ONE_WAY_PLAN = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
    ]
}


def rank_entry(jid: str) -> dict:
    journey = {
        "id": jid,
        "kind": "round_trip",
        "legs": [],
        "fit_facts": {},
        "preference_misses": [],
        "cost": {"mileage": {"by_program": {"united": 80000}}, "cash": []},
        "seat_sufficiency": "sufficient",
    }
    return {"journey": journey, "facts": {}, "verdicts": [], "cost_tier": 0}


def write_rank(
    slug: str, ids: list[str], *, notable: list | None = None, dropped: list | None = None
) -> None:
    doc = {
        "ranked": [rank_entry(i) for i in ids],
        "notable_stretches": notable or [],
        "dropped": dropped or [],
    }
    trips.artifact_write(slug, "rank.json", json.dumps(doc))


def write(slug: str, name: str, obj: object) -> None:
    trips.artifact_write(slug, name, json.dumps(obj))


def journey_ids(doc: dict) -> list[str]:
    return [e["journey"]["id"] for e in doc["journeys"]]


def test_ranked_journeys_are_the_board_capped_at_presentation_limit(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc())
    write_rank(slug, [f"J{i}" for i in range(8)])
    doc = factors.finalize(slug, now=clock())
    assert doc["trip_type"] == "round_trip"
    assert journey_ids(doc) == [f"J{i}" for i in range(6)]  # PRESENTATION_LIMIT


def test_finalists_have_no_separate_hybrids_class(getaway_home: Path) -> None:
    # Hybrids compose upstream at expand into the one journeys list — never a trailing class here.
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc())
    write_rank(slug, ["A", "B"])
    doc = factors.finalize(slug, now=clock())
    assert "hybrids" not in doc
    assert journey_ids(doc) == ["A", "B"]


def test_unpaired_leads_and_search_states_surface(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    lead = {
        "outbound": {"id": "OB", "dest": "NRT", "mileage": 70000},
        "return_search_state": {"state": "searched_empty"},
        "searched_at": None,
        "cache_age_hours": None,
    }
    states = {
        "outbound": {},
        "return": {"NRT": {"state": "partial", "reason": "page_budget", "has_more": True}},
    }
    write(slug, "expand.json", expand_doc(unpaired_outbounds=[lead], search_states=states))
    write_rank(slug, ["J0"])
    doc = factors.finalize(slug, now=clock())
    assert doc["unpaired_leads"] == [lead]  # trailing lead class, not a journey
    assert doc["search_states"] == states  # full by-leg map surfaced, never as "no space"


def test_beam_cut_truncation_rides_expand_provenance_to_the_board(getaway_home: Path) -> None:
    # A ≥3-leg composition overflow discloses provenance.truncation.beam_cut in expand.json;
    # finalize threads it onto the board like the shortlist's own truncation — never dropped.
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc(truncation={"beam_cut": 3}))
    write_rank(slug, ["J0"])
    doc = factors.finalize(slug, now=clock())
    assert doc["truncation"] == {"beam_cut": 3}


def test_no_truncation_key_when_expand_composed_within_the_beam(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc())  # no provenance.truncation
    write_rank(slug, ["J0"])
    doc = factors.finalize(slug, now=clock())
    assert "truncation" not in doc  # absent, not an empty stub


MULTI_CITY_PLAN = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "dests": ["NRT"]},
        {"id": "hop", "dests": ["BKK"], "stay_nights": {"min": 4, "max": 4}},
        {"id": "return", "dests": "$origins"},
    ]
}


def test_partial_leads_thread_from_expand_onto_the_board(getaway_home: Path) -> None:
    # A ≥3-leg plan with a dead middle market composes no journey; expand's partial-chain leads
    # thread onto the board verbatim — a zero-journey board with live leads is never a bare board.
    slug = _new(getaway_home, MULTI_CITY_PLAN)
    leads = [
        {
            "prefix": [{"role": "outbound", "id": "OB", "dest": "NRT", "mileage": 80000}],
            "reached": "NRT",
            "remaining": {
                "hop": {"state": "searched_empty"},
                "return": {"state": "not_run", "reason": "no_predecessor"},
            },
        }
    ]
    write(slug, "expand.json", expand_doc(leads=leads))
    write_rank(slug, [])  # the dead middle market yielded no bookable journey
    doc = factors.finalize(slug, now=clock())
    assert doc["journeys"] == []
    assert doc["partial_leads"] == leads  # honest per-remaining-leg states carried, never dropped


def test_no_partial_leads_key_when_expand_composed_a_full_chain(getaway_home: Path) -> None:
    slug = _new(getaway_home, MULTI_CITY_PLAN)
    write(slug, "expand.json", expand_doc())  # a full chain composed — no leads section
    write_rank(slug, ["J0"])
    doc = factors.finalize(slug, now=clock())
    assert "partial_leads" not in doc  # absent, not an empty stub


@pytest.mark.parametrize(
    "outbound_state",
    [
        pytest.param(
            {"NRT": {"state": "failed", "reason": "seats.aero 503", "retryability": "retryable"}},
            id="outbound-failed",
        ),
        pytest.param(
            {"NRT": {"state": "not_run", "reason": "quota_floor"}}, id="outbound-not-run-quota"
        ),
    ],
)
def test_one_way_outbound_failure_surfaces_in_finalists_not_empty_board(
    getaway_home: Path, outbound_state: dict
) -> None:
    # An outbound-only trip whose outbound sweep failed or quota-stopped reaches the board as its
    # honest per-endpoint state under the outbound leg — never a bare board that reads "no space".
    slug = _new(getaway_home, ONE_WAY_PLAN)
    states = {"outbound": outbound_state, "return": {}}
    write(slug, "expand.json", expand_doc(search_states=states))
    write_rank(slug, [])  # the failed sweep produced no bookable journeys
    doc = factors.finalize(slug, now=clock())
    assert doc["trip_type"] == "one_way"
    assert doc["journeys"] == []
    assert doc["search_states"] == states  # the outbound failure is carried, not dropped
    assert doc["search_states"]["outbound"]["NRT"]["state"] != "searched_empty"


def _lead(dest: str = "NRT", cabin: str = "J") -> dict:
    return {
        "outbound": {"id": "OB", "dest": dest, "cabin": cabin, "mileage": 70000},
        "return_search_state": {"state": "searched_empty", "verification": "unverified"},
        "searched_at": None,
        "cache_age_hours": 30.0,
    }


def test_lead_rescue_threads_onto_matching_unpaired_lead(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc(unpaired_outbounds=[_lead()]))
    write_rank(slug, ["J0"])
    # a background verifier found the return space the expired empty search had missed
    enhance.merge(
        slug,
        "verify",
        [
            {
                "target_id": "lead:NRT:J",
                "outcome": "confirmed",
                "checked_at": "2026-07-13T14:32:00+00:00",
                "method": "cookie",
                "observed": {"return_seats": 2},
                "evidence": "found J space NRT-SFO",
            }
        ],
    )
    doc = factors.finalize(slug, now=clock())
    assert doc["unpaired_leads"][0]["rescue"] == {
        "outcome": "confirmed",
        "checked_at": "2026-07-13T14:32:00+00:00",
        "observed": {"return_seats": 2},
        "evidence": "found J space NRT-SFO",
    }
    # annotation only — the lead never re-pairs into a journey.
    assert doc["unpaired_leads"][0]["outbound"]["id"] == "OB"
    assert journey_ids(doc) == ["J0"]  # journeys unchanged from the no-rescue board
    assert len(doc["unpaired_leads"]) == 1  # the lead stays in its own class, never re-paired


def test_lead_without_matching_verify_result_has_no_rescue(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc(unpaired_outbounds=[_lead(dest="NRT")]))
    write_rank(slug, ["J0"])
    enhance.merge(
        slug,
        "verify",
        [
            {
                "target_id": "lead:OKA:J",  # a different dest → no match for the NRT lead
                "outcome": "confirmed",
                "checked_at": "2026-07-13T14:32:00+00:00",
                "method": "cookie",
                "observed": {"return_seats": 2},
                "evidence": "found J space OKA-SFO",
            }
        ],
    )
    doc = factors.finalize(slug, now=clock())
    assert "rescue" not in doc["unpaired_leads"][0]


def test_notable_stretches_and_dropped_carry_through(getaway_home: Path) -> None:
    slug = _new(getaway_home, DIRECT_PLAN)
    write(slug, "expand.json", expand_doc())
    notable = [
        {
            "journey": {"id": "LATE"},
            "facts": {},
            "verdicts": [],
            "cost_tier": 1,
            "why": "suites, back Tuesday",
        }
    ]
    dropped = [{"journey_id": "TIGHT", "reason": "a leg's live seats are below the party"}]
    write_rank(slug, ["J0"], notable=notable, dropped=dropped)
    doc = factors.finalize(slug, now=clock())
    assert [n["journey"]["id"] for n in doc["notable_stretches"]] == ["LATE"]
    assert [d["journey_id"] for d in doc["dropped"]] == ["TIGHT"]
