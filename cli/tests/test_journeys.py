import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import shortlist_doc, sweep_envelope

from getaway import journeys, prefs, trips
from getaway.paths import cache_db
from getaway.store import QuotaFloorError, connect

FROZEN = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
WINDOW = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 7}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def seg(
    origin: str,
    dest: str,
    dep: str,
    arr: str,
    minutes: int = 600,
    cabin: str = "J",
    carrier: str = "UA",
) -> dict:
    return {
        "origin": origin,
        "dest": dest,
        "departs_local": dep,
        "arrives_local": arr,
        "duration_minutes": minutes,
        "cabin": cabin,
        "carrier": carrier,
        "flight_number": f"{carrier}1",
        "aircraft": "77W",
    }


def detail(cid: str, segments: list[dict], *, mileage: int = 80000, seats: int = 2) -> dict:
    return {
        "id": cid,
        "mileage": mileage,
        "total_taxes": 120,
        "taxes_currency": "USD",
        "remaining_seats": seats,
        "total_duration": sum(s["duration_minutes"] for s in segments),
        "segments": segments,
        "layovers": [],
        "booking_links": [{"label": "book", "link": "https://x", "primary": True}],
    }


def cand(
    cid: str,
    origin: str,
    dest: str,
    date: str,
    *,
    source: str = "united",
    cabin: str = "J",
    soft: bool = False,
    airlines: str = "UA",
    seats: int = 2,
) -> dict:
    return {
        "id": cid,
        "cabin": cabin,
        "date": date,
        "origin": origin,
        "dest": dest,
        "source": source,
        "mileage": 80000,
        "seats": seats,
        "airlines": airlines,
        "direct": True,
        "soft": soft,
        "departure_day_match": False,
    }


def seed(cid: str, d: dict) -> None:
    connect(cache_db(), now=clock()).trip_detail_put(cid, d)


def ob_detail(
    cid: str,
    dest: str = "NRT",
    dep: str = "2026-09-05T10:00",
    arr: str = "2026-09-06T14:00",
    mileage: int = 80000,
    seats: int = 2,
) -> dict:
    return detail(cid, [seg("SFO", dest, dep, arr)], mileage=mileage, seats=seats)


def ret_detail(
    cid: str,
    origin: str = "NRT",
    dep: str = "2026-09-12T16:00",
    arr: str = "2026-09-12T10:00",
    mileage: int = 80000,
    seats: int = 2,
) -> dict:
    return detail(cid, [seg(origin, "SFO", dep, arr)], mileage=mileage, seats=seats)


def make_trip(plan: dict, *, party: int = 2, cabin: str = "business") -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, {"cabin": cabin, "party": party, "window": WINDOW, "plan": plan})
    return SLUG


ROUND_TRIP = {
    "trip_type": "round_trip",
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
}
ONE_WAY = {
    "trip_type": "one_way",
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
}


def write_shortlists(
    slug: str,
    outbound: list[dict],
    ret: list[dict] | None = None,
    ret_states: dict | None = None,
    ob_states: dict | None = None,
) -> None:
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc(outbound, search_states=ob_states or {})),
    )
    if ret is not None:
        trips.artifact_write(
            slug,
            "legs/return/shortlist.json",
            json.dumps(shortlist_doc(ret, leg="return", search_states=ret_states or {})),
        )


@pytest.fixture
def home(getaway_home: Path) -> Path:
    return getaway_home


def test_round_trip_pairs_outbound_and_return(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug, [cand("OB", "SFO", "NRT", "2026-09-05")], [cand("RET", "NRT", "SFO", "2026-09-12")]
    )
    result = journeys.run(slug, now=clock())
    assert result == {"journeys": 1, "unpaired": 0, "gated": 0}
    doc = json.loads(trips.artifact_read(slug, "expand.json"))
    (journey,) = doc["journeys"]
    assert journey["kind"] == "round_trip"
    assert [leg["role"] for leg in journey["legs"]] == ["outbound", "return"]
    assert journey["seat_sufficiency"] == "sufficient"
    assert journey["fit_facts"]["trip_length_days"] == 7
    assert journey["cost"]["mileage"]["by_program"] == {"united": 160000}


def test_one_way_makes_a_journey_per_outbound_no_leads(home: Path) -> None:
    slug = make_trip(ONE_WAY)
    seed("OB1", ob_detail("OB1"))
    seed("OB2", ob_detail("OB2", dep="2026-09-07T10:00", arr="2026-09-08T14:00"))
    write_shortlists(
        slug, [cand("OB1", "SFO", "NRT", "2026-09-05"), cand("OB2", "SFO", "NRT", "2026-09-07")]
    )
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert {j["kind"] for j in doc["journeys"]} == {"one_way"}
    assert len(doc["journeys"]) == 2
    assert doc["unpaired_outbounds"] == []


@pytest.mark.parametrize(
    ("seats", "state", "gated"),
    [
        pytest.param(2, "sufficient", False, id="sufficient-visible"),
        pytest.param(0, "unknown", False, id="unknown-visible-with-warning"),
        pytest.param(1, "insufficient", True, id="insufficient-gated"),
    ],
)
def test_live_seat_sufficiency_gates_only_insufficient(
    home: Path, seats: int, state: str, gated: bool
) -> None:
    # The teaser always claims 2 seats; sufficiency is judged on the live-expanded return row.
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET", seats=seats))
    write_shortlists(
        slug,
        [cand("OB", "SFO", "NRT", "2026-09-05")],
        [cand("RET", "NRT", "SFO", "2026-09-12", seats=2)],
    )
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    if gated:
        assert doc["journeys"] == []
        assert len(doc["gated"]) == 1
        assert "below the party" in doc["gated"][0]["reason"]
    else:
        assert doc["gated"] == []
        (journey,) = doc["journeys"]
        assert journey["seat_sufficiency"] == state


def test_beyond_window_journey_composes_with_its_miss(home: Path) -> None:
    # The padded sweep surfaced an outbound that departs before the trip window opens; with the
    # window preference set, the composed journey carries the named miss (the renderer shows it).
    plan = {
        **ROUND_TRIP,
        "preferences": {
            "outbound_departure_window": {
                "value": {"start": "2026-09-01", "end": "2026-09-14"},
                "priority": "secondary",
            }
        },
    }
    slug = make_trip(plan)
    seed("OB", ob_detail("OB", dep="2026-08-28T10:00", arr="2026-08-29T14:00"))  # 4 days early
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug, [cand("OB", "SFO", "NRT", "2026-08-28")], [cand("RET", "NRT", "SFO", "2026-09-12")]
    )
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    (journey,) = doc["journeys"]
    misses = {m["code"]: m for m in journey["preference_misses"]}
    assert "outbound_departure_window" in misses  # composed, with the miss named
    assert misses["outbound_departure_window"]["delta"] == -4


def test_return_before_outbound_arrival_never_pairs(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB", dep="2026-09-10T10:00", arr="2026-09-11T14:00"))
    seed("RET", ret_detail("RET", dep="2026-09-05T16:00", arr="2026-09-05T10:00"))  # before arrival
    write_shortlists(
        slug,
        [cand("OB", "SFO", "NRT", "2026-09-10")],
        [cand("RET", "NRT", "SFO", "2026-09-05")],
        ret_states={"NRT": {"state": "complete"}},
    )
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert doc["journeys"] == []
    assert [lead["outbound"]["id"] for lead in doc["unpaired_outbounds"]] == ["OB"]


def test_unpaired_lead_trails_with_search_state_and_cache_age(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    write_shortlists(
        slug,
        [cand("OB", "SFO", "NRT", "2026-09-05")],
        ret=[],
        ret_states={"NRT": {"state": "searched_empty"}},
    )
    env = sweep_envelope()
    env["provenance"]["fetched_at"] = "2026-09-01T06:00:00+00:00"  # 6h before frozen now
    trips.artifact_write(slug, "legs/return/sweep.json", json.dumps(env))
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    (lead,) = doc["unpaired_outbounds"]
    assert lead["return_search_state"] == {"state": "searched_empty"}
    assert lead["searched_at"] == "2026-09-01T06:00:00+00:00"
    assert lead["cache_age_hours"] == 6.0


def test_expired_empty_return_reads_unverified(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    write_shortlists(
        slug,
        [cand("OB", "SFO", "NRT", "2026-09-05")],
        ret=[],
        ret_states={"NRT": {"state": "searched_empty"}},
    )
    env = sweep_envelope()
    env["provenance"]["fetched_at"] = (
        "2026-08-20T12:00:00+00:00"  # far older than the 24h sweep TTL
    )
    trips.artifact_write(slug, "legs/return/sweep.json", json.dumps(env))
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    (lead,) = doc["unpaired_outbounds"]
    assert lead["return_search_state"] == {"state": "searched_empty", "verification": "unverified"}


def test_partial_return_state_surfaces_not_as_no_space(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    states = {"NRT": {"state": "partial", "reason": "page_budget", "has_more": True}}
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")], ret=[], ret_states=states)
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert doc["search_states"] == {"outbound": {}, "return": states}  # partial rides through
    (lead,) = doc["unpaired_outbounds"]
    assert lead["return_search_state"]["state"] == "partial"


def test_one_way_outbound_failed_sweep_surfaces_not_as_no_space(home: Path) -> None:
    # A one-way whose outbound sweep failed carries that failed state into expand's by-leg map,
    # never an empty search_states reading as a hard "no availability" board.
    slug = make_trip(ONE_WAY)
    ob_states = {"NRT": {"state": "failed", "reason": "http_503", "retryability": "retryable"}}
    write_shortlists(slug, [], ob_states=ob_states)
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert doc["journeys"] == []
    assert doc["search_states"] == {"outbound": ob_states, "return": {}}
    assert doc["provenance"]["quota_stopped"] is False  # a failed sweep, not a quota halt


def test_one_way_outbound_quota_stopped_sweep_distinguishes_from_empty(home: Path) -> None:
    # A quota-halted outbound sweep surfaces its not_run state, distinct from a genuine empty.
    slug = make_trip(ONE_WAY)
    ob_states = {"NRT": {"state": "not_run", "reason": "quota_budget"}}
    write_shortlists(slug, [], ob_states=ob_states)
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert doc["journeys"] == []
    assert doc["search_states"]["outbound"]["NRT"] == {"state": "not_run", "reason": "quota_budget"}
    assert doc["search_states"]["outbound"]["NRT"]["state"] != "searched_empty"


def test_no_itinerary_in_cabin_marks_leg_failed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from getaway.store import NoData

    class _NoItinerary:
        def __init__(self, store: object, floor: int) -> None:
            pass

        def trip_detail(self, cid: str, cabin: str) -> dict:
            raise NoData(f"no {cabin} itinerary for {cid}")

    monkeypatch.setattr(journeys, "SeatsClient", _NoItinerary)
    slug = make_trip(ONE_WAY)  # nothing seeded -> live fetch -> NoData for the missing cabin
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")])
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert doc["journeys"] == []
    assert doc["leg_states"]["outbound:OB:J"] == {
        "state": "failed",
        "reason": "no_itinerary_in_cabin",
    }


@pytest.mark.parametrize(
    ("cached", "journeys_count", "leg_state"),
    [
        pytest.param(
            detail("OB", []),
            0,
            {"state": "failed", "reason": "no_itinerary_in_cabin"},
            id="empty-segment-cache-skips-without-crash",
        ),
        pytest.param(
            ob_detail("OB"),
            1,
            {"state": "expanded"},
            id="real-detail-control-expands",
        ),
    ],
)
def test_empty_segment_cached_detail_reads_as_no_itinerary(
    home: Path, cached: dict, journeys_count: int, leg_state: dict
) -> None:
    # An empty-segment cache must skip, not pass _detail_matches_cabin (all([]) is True) and crash.
    slug = make_trip(ONE_WAY)
    seed("OB", cached)
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")])
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert len(doc["journeys"]) == journeys_count
    assert doc["leg_states"]["outbound:OB:J"] == leg_state


def test_quota_floor_stops_expand_unstamped(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    slug = make_trip(ONE_WAY)
    connect(cache_db(), now=clock()).record_quota("/trips", 50)  # below the default floor of 100
    write_shortlists(
        slug, [cand("OB", "SFO", "NRT", "2026-09-05")]
    )  # no detail seeded -> live fetch
    with pytest.raises(QuotaFloorError):
        journeys.run(slug, now=clock())
    doc = json.loads(trips.artifact_read(slug, "expand.json"))
    assert doc["provenance"]["quota_stopped"] is True
    assert doc["leg_states"]["outbound:OB:J"] == {"state": "not_run", "reason": "quota_floor"}
    assert doc["search_states"] == {"outbound": {}, "return": {}}  # by-leg map even on a quota stop
    assert (
        trips.phase_check(slug, "expand", now=clock())[1] is None
    )  # node left unstamped to resume


# --- Hybrid composition (gateway award + cash bridge / two-award), unified into journeys ----------

HYBRID_SPEC = {"gateways": ["NRT", "ICN"], "onward_dests": ["OKA", "KIX"], "max_hybrids": 4}
HYBRID_ONE_WAY = {**ONE_WAY, "hybrid": HYBRID_SPEC}
HYBRID_ROUND_TRIP = {**ROUND_TRIP, "hybrid": HYBRID_SPEC}


def gw_cand(cid: str, dest: str, mileage: int = 80000, *, seats: int = 2) -> dict:
    c = cand(cid, "SFO", dest, "2026-09-05", source="aeroplan", cabin="J", seats=seats)
    c["mileage"] = mileage
    return c


def onward_min(gateway: str, dest: str, mileage: int, *, date: str = "2026-09-08") -> dict:
    return {
        "gateway": gateway,
        "onward_dest": dest,
        "cabin": "economy",
        "id": f"OW-{gateway}-{dest}",
        "date": date,
        "source": "aeroplan",
        "mileage": mileage,
        "seats": 2,
        "airlines": "NH",
        "direct": True,
    }


def onward_detail(
    cid: str, gateway: str, dest: str, *, mileage: int = 30000, seats: int = 2
) -> dict:
    segs = [seg(gateway, dest, "2026-09-08T09:00", "2026-09-08T12:00", cabin="Y")]
    return detail(cid, segs, mileage=mileage, seats=seats)


def cash_quote(
    gateway: str,
    dest: str,
    price: float,
    *,
    date: str = "2026-09-08",
    cabin: str = "economy",
    departs_local: str = "2026-09-08T09:00",
    arrives_local: str = "2026-09-08T12:00",
    stops: int = 0,
    connections: list[str] | None = None,
) -> dict:
    return {
        "gateway": gateway,
        "onward_dest": dest,
        "date": date,
        "cabin": cabin,
        "price": price,
        "currency": "USD",
        "duration_minutes": 180,
        "stops": stops,
        "connections": connections or [],
        "airline": "Japan Airlines",
        "flight_number": "JL1",
        "departs_local": departs_local,
        "arrives_local": arrives_local,
        "source": "fli",
    }


def bpair(gateway: str, dest: str, date: str = "2026-09-08") -> dict:
    return {"gateway": gateway, "onward_dest": dest, "date": date}


def write_hybrid(
    slug: str,
    *,
    gateways: list[dict],
    pairs: list[dict],
    quotes: list[dict],
    minima: list[dict] | None = None,
    with_bridge: bool = True,
) -> None:
    trips.artifact_write(
        slug, "legs/outbound/shortlist-gateway.json", json.dumps(shortlist_doc(gateways))
    )
    trips.artifact_write(
        slug,
        "legs/outbound/onward.json",
        json.dumps({"minima": minima or [], "bridge_pairs": pairs}),
    )
    if with_bridge:
        trips.artifact_write(
            slug, "legs/outbound/bridge.json", json.dumps({"quotes": quotes, "failures": []})
        )


def _run(slug: str) -> dict:
    journeys.run(slug, now=clock())
    return json.loads(trips.artifact_read(slug, "expand.json"))


def test_gateway_cash_hybrid_composes_typed_legs(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_shortlists(slug, [])  # no direct outbound — isolate the hybrid
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["kind"] == "gateway_cash"
    assert [(leg["role"], leg["mode"]) for leg in journey["legs"]] == [
        ("outbound", "award"),
        ("onward", "cash"),
    ]
    assert journey["cost"]["mileage"]["by_program"] == {"aeroplan": 80000}
    assert journey["cost"]["cash"] == [
        {
            "leg_role": "onward",
            "amount_cents": 12000,
            "currency": "USD",
            "duration_minutes": 180,
            "airline": "Japan Airlines",
        }
    ]
    assert journey["seat_sufficiency"] == "sufficient"  # judged on the award gateway; cash skipped
    onward_fact = next(f for f in journey["fit_facts"]["legs"] if f["role"] == "onward")
    assert onward_fact["arrives_local"] == "2026-09-08T12:00"  # real observed hop arrival threaded


def test_bridge_quotes_keyed_by_date_not_just_pair(home: Path) -> None:
    # Two priced bridge quotes for the same (gateway, onward_dest) on different dates must not
    # collapse onto each other — each bridge_pair spec gets its own date's quote.
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA", "2026-09-08"), bpair("NRT", "OKA", "2026-09-15")],
        quotes=[
            cash_quote(
                "NRT",
                "OKA",
                120.0,
                date="2026-09-08",
                departs_local="2026-09-08T09:00",
                arrives_local="2026-09-08T12:00",
            ),
            cash_quote(
                "NRT",
                "OKA",
                200.0,
                date="2026-09-15",
                departs_local="2026-09-15T09:00",
                arrives_local="2026-09-15T12:00",
            ),
        ],
    )
    doc = _run(slug)
    by_date = {j["legs"][1]["id"].rsplit(":", 1)[-1]: j for j in doc["journeys"]}
    assert set(by_date) == {"2026-09-08", "2026-09-15"}
    assert by_date["2026-09-08"]["cost"]["cash"][0]["amount_cents"] == 12000
    assert by_date["2026-09-15"]["cost"]["cash"][0]["amount_cents"] == 20000
    onward_08 = next(f for f in by_date["2026-09-08"]["fit_facts"]["legs"] if f["role"] == "onward")
    onward_15 = next(f for f in by_date["2026-09-15"]["fit_facts"]["legs"] if f["role"] == "onward")
    assert onward_08["departs_local"] == "2026-09-08T09:00"
    assert onward_15["departs_local"] == "2026-09-15T09:00"


def test_two_award_hybrid_composes_both_award_legs(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    seed("OW-NRT-OKA", onward_detail("OW-NRT-OKA", "NRT", "OKA", mileage=30000))
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        minima=[onward_min("NRT", "OKA", 30000)],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )
    doc = _run(slug)
    assert {j["kind"] for j in doc["journeys"]} == {"gateway_cash", "gateway_award"}
    two_award = next(j for j in doc["journeys"] if j["kind"] == "gateway_award")
    assert [(leg["role"], leg["mode"]) for leg in two_award["legs"]] == [
        ("outbound", "award"),
        ("onward", "award"),
    ]
    assert two_award["cost"]["mileage"]["by_program"] == {"aeroplan": 110000}  # 80000 + 30000
    assert two_award["cost"]["cash"] == []


def test_hybrid_pair_without_cash_quote_composes_nothing(home: Path) -> None:
    # Inherited coupling: a pair with no priced bridge yields no hybrid, cash or two-award.
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    seed("OW-NRT-OKA", onward_detail("OW-NRT-OKA", "NRT", "OKA"))
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        minima=[onward_min("NRT", "OKA", 30000)],
        pairs=[bpair("NRT", "OKA")],
        quotes=[],
    )
    assert _run(slug)["journeys"] == []


def test_absent_bridge_yields_no_cash_hybrids_directs_survive(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    seed("OB", ob_detail("OB", dest="NRT"))
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[],
        with_bridge=False,  # bridge failed/absent
    )
    doc = _run(slug)
    assert [j["kind"] for j in doc["journeys"]] == ["one_way"]  # direct survives, zero cash hybrids


def test_max_hybrids_caps_composition_cheapest_first(home: Path) -> None:
    slug = make_trip({**HYBRID_ONE_WAY, "hybrid": {**HYBRID_SPEC, "max_hybrids": 1}})
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    seed("GW-ICN", ob_detail("GW-ICN", dest="ICN"))
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT", 80000), gw_cand("GW-ICN", "ICN", 90000)],
        pairs=[bpair("NRT", "OKA"), bpair("ICN", "KIX")],
        quotes=[cash_quote("NRT", "OKA", 120.0), cash_quote("ICN", "KIX", 150.0)],
    )
    doc = _run(slug)
    composed = [(j["kind"], j["legs"][0]["id"]) for j in doc["journeys"]]
    assert composed == [("gateway_cash", "GW-NRT")]


def test_hybrid_gates_on_insufficient_award_gateway(home: Path) -> None:
    # The cash leg carries no seats row; sufficiency is judged on the award gateway alone.
    slug = make_trip(HYBRID_ONE_WAY, party=2)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", seats=1))  # below the party of 2
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT", seats=1)],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )
    doc = _run(slug)
    assert doc["journeys"] == []
    assert len(doc["gated"]) == 1


def test_round_trip_hybrid_pairs_return_with_cash_onward(home: Path) -> None:
    slug = make_trip(HYBRID_ROUND_TRIP)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", dep="2026-09-05T10:00", arr="2026-09-06T14:00"))
    seed("RET-OKA", ret_detail("RET-OKA", origin="OKA"))
    write_shortlists(slug, [], ret=[cand("RET-OKA", "OKA", "SFO", "2026-09-12")])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["kind"] == "gateway_cash"
    assert [leg["role"] for leg in journey["legs"]] == ["outbound", "onward", "return"]
    fit_facts = journey["fit_facts"]
    assert fit_facts["away_nights"] == 4  # cash arrival 09-08 -> return departure 09-12
    assert fit_facts["trip_length_days"] == 7  # 09-05 gateway departure -> 09-12 return arrival


def test_cash_onward_return_same_day_before_arrival_rejected(home: Path) -> None:
    # The return departs the same day as, but before, the cash leg's real arrival clock —
    # structurally impossible, so no journey composes.
    slug = make_trip(HYBRID_ROUND_TRIP)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", dep="2026-09-05T10:00", arr="2026-09-06T14:00"))
    seed(
        "RET-OKA",
        ret_detail("RET-OKA", origin="OKA", dep="2026-09-08T08:00", arr="2026-09-08T20:00"),
    )
    write_shortlists(slug, [], ret=[cand("RET-OKA", "OKA", "SFO", "2026-09-08")])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0, arrives_local="2026-09-08T12:00")],
    )
    doc = _run(slug)
    assert doc["journeys"] == []


def test_cash_onward_return_next_morning_accepted(home: Path) -> None:
    # The return departs the morning after the cash leg's real arrival clock — structurally fine.
    slug = make_trip(HYBRID_ROUND_TRIP)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", dep="2026-09-05T10:00", arr="2026-09-06T14:00"))
    seed(
        "RET-OKA",
        ret_detail("RET-OKA", origin="OKA", dep="2026-09-09T08:00", arr="2026-09-09T20:00"),
    )
    write_shortlists(slug, [], ret=[cand("RET-OKA", "OKA", "SFO", "2026-09-09")])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0, arrives_local="2026-09-08T12:00")],
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["kind"] == "gateway_cash"


@pytest.mark.parametrize(
    ("segments", "dest", "avoided"),
    [
        pytest.param(
            [
                seg("SFO", "ICN", "2026-09-05T10:00", "2026-09-06T14:00"),
                seg("ICN", "NRT", "2026-09-06T16:00", "2026-09-06T18:00"),
            ],
            "NRT",
            "ICN",
            id="same-airport-connection",
        ),
        pytest.param(
            [
                seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00"),
                seg("HND", "OKA", "2026-09-06T16:00", "2026-09-06T18:00"),
            ],
            "OKA",
            "HND",
            id="airport-change-self-transfer",
        ),
    ],
)
def test_avoid_transit_gates_award_leg_connection_with_named_reason(
    home: Path, segments: list[dict], dest: str, avoided: str
) -> None:
    slug = make_trip(ONE_WAY)
    prefs.set_patch({"avoid_transit": [avoided]})
    seed("OB", detail("OB", segments))
    write_shortlists(slug, [cand("OB", "SFO", dest, "2026-09-05")])

    doc = _run(slug)

    assert doc["journeys"] == []
    assert doc["gated"] == [
        {"journey_id": "outbound:OB:J", "reason": f"transits {avoided}, which you avoid"}
    ]


def test_avoid_transit_gates_hybrid_boundary_with_named_reason(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    prefs.set_patch({"avoid_transit": ["NRT"]})
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )

    doc = _run(slug)

    assert doc["journeys"] == []
    assert doc["gated"] == [
        {
            "journey_id": "outbound:GW-NRT:J|onward:cash:NRT:OKA:2026-09-08:economy",
            "reason": "transits NRT, which you avoid",
        }
    ]


CASH_HOP_JID = "outbound:GW-NRT:J|onward:cash:NRT:OKA:2026-09-08:economy"


def _write_cash_hop(slug: str, connections: list[str]) -> None:
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_shortlists(slug, [])
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        pairs=[bpair("NRT", "OKA")],
        quotes=[cash_quote("NRT", "OKA", 120.0, stops=len(connections), connections=connections)],
    )


def test_cash_leg_carries_connections_on_leg_and_fit_facts(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    _write_cash_hop(slug, ["FUK"])

    doc = _run(slug)

    journey = doc["journeys"][0]
    cash_leg = journey["legs"][-1]
    assert cash_leg["mode"] == "cash"
    assert cash_leg["cash"]["connections"] == ["FUK"]
    cash_facts = journey["fit_facts"]["legs"][-1]
    assert cash_facts["mode"] == "cash"
    assert cash_facts["connections"] == ["FUK"]


def test_avoid_transit_gates_cash_leg_internal_connection(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    prefs.set_patch({"avoid_transit": ["FUK"]})  # a stop inside the priced cash hop, not a boundary
    _write_cash_hop(slug, ["FUK"])

    doc = _run(slug)

    assert doc["journeys"] == []
    assert doc["gated"] == [
        {"journey_id": CASH_HOP_JID, "reason": "transits FUK, which you avoid"}
    ]


@pytest.mark.parametrize(
    "avoided",
    ["SFO", "OKA"],
    ids=["journey-origin", "cash-onward-destination"],
)
def test_avoid_transit_ignores_cash_hop_endpoints(home: Path, avoided: str) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    prefs.set_patch({"avoid_transit": [avoided]})
    _write_cash_hop(slug, ["FUK"])

    doc = _run(slug)

    assert doc["gated"] == []
    assert [journey["id"] for journey in doc["journeys"]] == [CASH_HOP_JID]


@pytest.mark.parametrize(
    ("plan", "avoided", "return_leg", "expected_journey_id"),
    [
        pytest.param(ONE_WAY, "SFO", False, "outbound:OB:J", id="journey-origin"),
        pytest.param(ONE_WAY, "NRT", False, "outbound:OB:J", id="one-way-destination"),
        pytest.param(
            ROUND_TRIP,
            "NRT",
            True,
            "outbound:OB:J|return:RET:J",
            id="round-trip-turnaround",
        ),
    ],
)
def test_avoid_transit_does_not_gate_trip_endpoints(
    home: Path,
    plan: dict,
    avoided: str,
    return_leg: bool,
    expected_journey_id: str,
) -> None:
    slug = make_trip(plan)
    prefs.set_patch({"avoid_transit": [avoided]})
    seed("OB", ob_detail("OB"))
    returns = None
    if return_leg:
        seed("RET", ret_detail("RET"))
        returns = [cand("RET", "NRT", "SFO", "2026-09-12")]
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")], returns)

    doc = _run(slug)

    assert doc["gated"] == []
    assert [journey["id"] for journey in doc["journeys"]] == [expected_journey_id]
