import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import shortlist_doc, sweep_envelope

from getaway import journeys, prefs, trips
from getaway.paths import UsageError, cache_db
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
    mileage: int = 80000,
) -> dict:
    return {
        "id": cid,
        "cabin": cabin,
        "date": date,
        "origin": origin,
        "dest": dest,
        "source": source,
        "mileage": mileage,
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


# A two-intent round trip and a single-intent one-way — the canonical shapes, pinned field-by-field
# against HEAD's pairing output (ids, roles, costs, trip_length), not by a literal v2 byte capture.
ROUND_TRIP = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "return", "dests": "$origins"},
    ]
}
ONE_WAY = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
    ]
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


def test_legless_plan_raises_typed_usage_error(home: Path) -> None:
    prefs.init()
    trips.new(SLUG, now=clock())
    with pytest.raises(UsageError, match="plan.legs must be a non-empty list"):
        journeys.run(SLUG, now=clock())


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
    assert journey["kind"] == "award→award"
    assert journey["id"] == "outbound:OB:J|return:RET:J"
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
    assert {j["kind"] for j in doc["journeys"]} == {"award"}
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
    assert doc["search_states"] == {"outbound": ob_states}  # per-leg-id; no return leg on a one-way
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


def test_mixed_cabin_cached_detail_expands(home: Path) -> None:
    slug = make_trip(ONE_WAY)
    segments = [
        seg("SFO", "ORD", "2026-09-05T10:00", "2026-09-05T16:00", cabin="J"),
        seg("ORD", "NRT", "2026-09-05T18:00", "2026-09-06T14:00", cabin="Y"),
    ]
    seed("OB", detail("OB", segments))
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")])
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert len(doc["journeys"]) == 1
    assert doc["leg_states"]["outbound:OB:J"] == {"state": "expanded"}
    expanded_segments = doc["journeys"][0]["legs"][0]["detail"]["segments"]
    assert [segment["cabin"] for segment in expanded_segments] == ["J", "Y"]


def test_cached_detail_without_requested_cabin_marks_leg_failed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from getaway.store import NoData

    class _NoItinerary:
        def __init__(self, store: object, floor: int) -> None:
            pass

        def trip_detail(self, cid: str, cabin: str) -> dict:
            raise NoData(f"no {cabin} itinerary for {cid}")

    monkeypatch.setattr(journeys, "SeatsClient", _NoItinerary)
    slug = make_trip(ONE_WAY)
    seed(
        "OB",
        detail(
            "OB",
            [
                seg("SFO", "ORD", "2026-09-05T10:00", "2026-09-05T16:00", cabin="Y"),
                seg("ORD", "NRT", "2026-09-05T18:00", "2026-09-06T14:00", cabin="F"),
            ],
        ),
    )
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
    # An empty-segment cache must skip without falling through to a live fetch.
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
    assert doc["search_states"] == {"outbound": {}}  # by-leg map even on a quota stop
    assert (
        trips.phase_check(slug, "expand", now=clock())[1] is None
    )  # node left unstamped to resume


# --- Gateway hybrid (award gateway + either-mode onward hop), composed as an ordinary chain -------

HYBRID_ONE_WAY = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA", "KIX"], "mode": "either"},
    ]
}
HYBRID_ROUND_TRIP = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA", "KIX"], "mode": "either"},
        {"id": "return", "dests": "$origins"},
    ]
}


def gw_cand(cid: str, dest: str, mileage: int = 80000, *, seats: int = 2) -> dict:
    return cand(
        cid, "SFO", dest, "2026-09-05", source="aeroplan", cabin="J", seats=seats, mileage=mileage
    )


def onward_award(
    cid: str,
    gateway: str,
    dest: str,
    *,
    mileage: int = 30000,
    seats: int = 2,
    date: str = "2026-09-08",
) -> dict:
    return cand(
        cid, gateway, dest, date, source="aeroplan", cabin="Y", seats=seats, mileage=mileage
    )


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


def write_hybrid(
    slug: str,
    *,
    gateways: list[dict],
    quotes: list[dict],
    onward_awards: list[dict] | None = None,
    with_bridge: bool = True,
) -> None:
    """The outbound leg's own shortlist carries the gateway award candidates; the onward leg's
    shortlist carries its award options and its bridge carries the priced cash quotes."""
    trips.artifact_write(slug, "legs/outbound/shortlist.json", json.dumps(shortlist_doc(gateways)))
    if onward_awards is not None:
        trips.artifact_write(
            slug,
            "legs/onward/shortlist.json",
            json.dumps(shortlist_doc(onward_awards, leg="onward")),
        )
    if with_bridge:
        trips.artifact_write(
            slug, "legs/onward/bridge.json", json.dumps({"quotes": quotes, "failures": []})
        )


def _run(slug: str) -> dict:
    journeys.run(slug, now=clock())
    return json.loads(trips.artifact_read(slug, "expand.json"))


def test_gateway_cash_hybrid_composes_typed_legs(home: Path) -> None:
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_hybrid(
        slug, gateways=[gw_cand("GW-NRT", "NRT")], quotes=[cash_quote("NRT", "OKA", 120.0)]
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["kind"] == "award→cash"
    assert journey["id"] == "outbound:GW-NRT:J|onward:cash:NRT:OKA:2026-09-08:economy"
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
    # collapse onto each other — each becomes its own composed cash hop.
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        quotes=[
            cash_quote(
                "NRT", "OKA", 120.0, date="2026-09-08",
                departs_local="2026-09-08T09:00", arrives_local="2026-09-08T12:00",
            ),
            cash_quote(
                "NRT", "OKA", 200.0, date="2026-09-15",
                departs_local="2026-09-15T09:00", arrives_local="2026-09-15T12:00",
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


def test_either_onward_composes_both_award_and_cash_variants(home: Path) -> None:
    # An either onward carries an award option (its own shortlist) and a cash option (its bridge);
    # the chain-builder composes one journey for each, decoupled — no cash needed to surface award.
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    seed("OW-NRT-OKA", onward_detail("OW-NRT-OKA", "NRT", "OKA", mileage=30000))
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        onward_awards=[onward_award("OW-NRT-OKA", "NRT", "OKA", mileage=30000)],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )
    doc = _run(slug)
    assert {j["kind"] for j in doc["journeys"]} == {"award→cash", "award→award"}
    two_award = next(j for j in doc["journeys"] if j["kind"] == "award→award")
    assert [(leg["role"], leg["mode"]) for leg in two_award["legs"]] == [
        ("outbound", "award"),
        ("onward", "award"),
    ]
    assert two_award["cost"]["mileage"]["by_program"] == {"aeroplan": 110000}  # 80000 + 30000
    assert two_award["cost"]["cash"] == []


def test_either_onward_award_survives_absent_bridge(home: Path) -> None:
    # Decoupled from HEAD's coupling: an award onward composes even with no priced cash bridge.
    slug = make_trip(HYBRID_ONE_WAY)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    seed("OW-NRT-OKA", onward_detail("OW-NRT-OKA", "NRT", "OKA"))
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        onward_awards=[onward_award("OW-NRT-OKA", "NRT", "OKA", mileage=30000)],
        quotes=[],
        with_bridge=False,  # bridge failed/absent
    )
    doc = _run(slug)
    assert [j["kind"] for j in doc["journeys"]] == ["award→award"]  # award onward survives, no cash


def test_beam_caps_three_leg_composition_cheapest_first_and_discloses_cut(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The beam bounds only ≥3-leg plans. Two full chains compose; width 1 keeps the cheaper one, the
    # cut is disclosed as composition truncation in provenance, and no chain becomes a false lead.
    monkeypatch.setattr(journeys, "COMPOSE_BEAM_WIDTH", 1)
    slug = make_trip(HYBRID_ROUND_TRIP)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))
    seed("GW-ICN", ob_detail("GW-ICN", dest="ICN", mileage=90000))
    seed("RET-OKA", ret_detail("RET-OKA", origin="OKA"))
    seed("RET-KIX", ret_detail("RET-KIX", origin="KIX"))
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(
            shortlist_doc(
                [
                    cand("RET-OKA", "OKA", "SFO", "2026-09-12"),
                    cand("RET-KIX", "KIX", "SFO", "2026-09-12"),
                ],
                leg="return",
            )
        ),
    )
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT", 80000), gw_cand("GW-ICN", "ICN", 90000)],
        quotes=[cash_quote("NRT", "OKA", 120.0), cash_quote("ICN", "KIX", 150.0)],
    )
    doc = _run(slug)
    composed = [(j["kind"], j["legs"][0]["id"]) for j in doc["journeys"]]
    assert composed == [("award→cash→award", "GW-NRT")]  # the cheapest chain survives the beam
    assert doc["provenance"]["truncation"] == {"beam_cut": 1}  # the cut is disclosed, not hidden
    assert doc["unpaired_outbounds"] == []  # a beam-cut ≥3-leg chain is truncation, never a lead


def test_hybrid_gates_on_insufficient_award_gateway(home: Path) -> None:
    # The cash leg carries no seats row; sufficiency is judged on the award gateway alone.
    slug = make_trip(HYBRID_ONE_WAY, party=2)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", seats=1))  # below the party of 2
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT", seats=1)],
        quotes=[cash_quote("NRT", "OKA", 120.0)],
    )
    doc = _run(slug)
    assert doc["journeys"] == []
    assert len(doc["gated"]) == 1


def test_round_trip_hybrid_pairs_return_with_cash_onward(home: Path) -> None:
    slug = make_trip(HYBRID_ROUND_TRIP)
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", dep="2026-09-05T10:00", arr="2026-09-06T14:00"))
    seed("RET-OKA", ret_detail("RET-OKA", origin="OKA"))
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET-OKA", "OKA", "SFO", "2026-09-12")], leg="return")),
    )
    write_hybrid(
        slug, gateways=[gw_cand("GW-NRT", "NRT")], quotes=[cash_quote("NRT", "OKA", 120.0)]
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["kind"] == "award→cash→award"
    assert [leg["role"] for leg in journey["legs"]] == ["outbound", "onward", "return"]
    fit_facts = journey["fit_facts"]
    assert fit_facts["away_nights"] == 4  # cash arrival 09-08 -> return departure 09-12
    assert fit_facts["trip_length_days"] == 7  # 09-05 gateway departure -> 09-12 return arrival


def _write_hybrid_round_trip_return(slug: str, ret_dep: str, ret_arr: str, ret_date: str) -> None:
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT", dep="2026-09-05T10:00", arr="2026-09-06T14:00"))
    seed("RET-OKA", ret_detail("RET-OKA", origin="OKA", dep=ret_dep, arr=ret_arr))
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET-OKA", "OKA", "SFO", ret_date)], leg="return")),
    )
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
        quotes=[cash_quote("NRT", "OKA", 120.0, arrives_local="2026-09-08T12:00")],
    )


def test_cash_onward_return_same_day_before_arrival_rejected(home: Path) -> None:
    # The return departs the same day as, but before, the cash leg's real arrival clock —
    # structurally impossible, so no journey composes.
    slug = make_trip(HYBRID_ROUND_TRIP)
    _write_hybrid_round_trip_return(slug, "2026-09-08T08:00", "2026-09-08T20:00", "2026-09-08")
    assert _run(slug)["journeys"] == []


def test_cash_onward_return_next_morning_accepted(home: Path) -> None:
    # The return departs the morning after the cash leg's real arrival clock — structurally fine.
    slug = make_trip(HYBRID_ROUND_TRIP)
    _write_hybrid_round_trip_return(slug, "2026-09-09T08:00", "2026-09-09T20:00", "2026-09-09")
    (journey,) = _run(slug)["journeys"]
    assert journey["kind"] == "award→cash→award"


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
    write_hybrid(
        slug, gateways=[gw_cand("GW-NRT", "NRT")], quotes=[cash_quote("NRT", "OKA", 120.0)]
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
    write_hybrid(
        slug,
        gateways=[gw_cand("GW-NRT", "NRT")],
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


# --- Multi-city chains and leading positioning legs (new leg-intent shapes) -----------------------

MULTI_CITY = {
    "legs": [
        {
            "id": "outbound", "origins": ["SFO"], "dests": ["NRT"],
            "stay_nights": {"min": 4, "max": 4},
        },
        {"id": "hop", "dests": ["BKK"], "stay_nights": {"min": 4, "max": 4}},
        {"id": "return", "dests": "$origins"},
    ]
}


def test_multi_city_chain_composes_with_two_stay_boundaries(home: Path) -> None:
    slug = make_trip(MULTI_CITY)
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    seed("HOP", detail("HOP", [seg("NRT", "BKK", "2026-09-10T10:00", "2026-09-10T14:00")]))
    seed("RET", detail("RET", [seg("BKK", "SFO", "2026-09-14T16:00", "2026-09-14T20:00")]))
    trips.artifact_write(
        slug, "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug, "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([cand("HOP", "NRT", "BKK", "2026-09-10")], leg="hop")),
    )
    trips.artifact_write(
        slug, "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET", "BKK", "SFO", "2026-09-14")], leg="return")),
    )
    (journey,) = _run(slug)["journeys"]
    assert journey["kind"] == "award→award→award · 2 stays"
    assert [leg["role"] for leg in journey["legs"]] == ["outbound", "hop", "return"]
    assert journey["fit_facts"]["trip_length_days"] == 9  # 09-05 departure -> 09-14 arrival


def test_multi_city_rejects_stay_violating_hop(home: Path) -> None:
    # A hop that departs one night after arrival violates the declared 4-night stay — no chain.
    slug = make_trip(MULTI_CITY)
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    seed("HOP", detail("HOP", [seg("NRT", "BKK", "2026-09-07T10:00", "2026-09-07T14:00")]))  # +1n
    seed("RET", detail("RET", [seg("BKK", "SFO", "2026-09-11T16:00", "2026-09-11T20:00")]))
    trips.artifact_write(
        slug, "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug, "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([cand("HOP", "NRT", "BKK", "2026-09-07")], leg="hop")),
    )
    trips.artifact_write(
        slug, "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET", "BKK", "SFO", "2026-09-11")], leg="return")),
    )
    assert _run(slug)["journeys"] == []


POSITIONING = {
    "legs": [
        {"id": "positioning", "origins": ["SFO"], "dests": ["LAX"], "mode": "cash",
         "optional": True, "role": "positioning"},
        {"id": "onward", "dests": ["NRT"]},
    ]
}


def test_positioning_cash_leg_composes_as_an_ordinary_chain_leg(home: Path) -> None:
    slug = make_trip(POSITIONING)
    seed("ONW", detail("ONW", [seg("LAX", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    trips.artifact_write(
        slug, "legs/positioning/bridge.json",
        json.dumps(
            {
                "quotes": [
                    cash_quote(
                        "SFO", "LAX", 90.0, date="2026-09-03",
                        departs_local="2026-09-03T08:00", arrives_local="2026-09-03T11:00",
                    )
                ],
                "failures": [],
            }
        ),
    )
    trips.artifact_write(
        slug, "legs/onward/shortlist.json",
        json.dumps(shortlist_doc([cand("ONW", "LAX", "NRT", "2026-09-05")], leg="onward")),
    )
    (journey,) = _run(slug)["journeys"]
    assert journey["kind"] == "cash→award"
    assert [(leg["role"], leg["mode"]) for leg in journey["legs"]] == [
        ("positioning", "cash"),
        ("onward", "award"),
    ]
    assert journey["fit_facts"]["trip_length_days"] is None  # no homeward leg, one-way shape
    # The positioning cash leg contributes no cabin fact and no cabin miss.
    positioning_fact = journey["fit_facts"]["legs"][0]
    assert positioning_fact["mode"] == "cash"
    assert "cabin" not in positioning_fact
    assert all(m["code"] != "cabin" for m in journey["preference_misses"])
    assert journey["cost"]["cash"][0]["amount_cents"] == 9000


# --- degeneracy gate: two-leg exhaustive vs ≥3-leg beam, and dateline continuity -----------------


def test_two_leg_round_trip_composes_exhaustively_ignoring_beam(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The beam bounds only ≥3-leg plans: a two-leg round trip pairs all 9x8 = 72, no cut, no leads.
    monkeypatch.setattr(journeys, "COMPOSE_BEAM_WIDTH", 2)
    slug = make_trip(ROUND_TRIP)
    obs, rets = [], []
    for i in range(9):
        cid = f"OB{i}"
        seed(cid, ob_detail(cid))
        obs.append(cand(cid, "SFO", "NRT", "2026-09-05"))
    for j in range(8):
        cid = f"RET{j}"
        seed(cid, ret_detail(cid))
        rets.append(cand(cid, "NRT", "SFO", "2026-09-12"))
    write_shortlists(slug, obs, rets, ret_states={"NRT": {"state": "complete"}})
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert len(doc["journeys"]) == 72
    assert doc["unpaired_outbounds"] == []
    assert "truncation" not in doc["provenance"]


def test_two_leg_round_trip_composes_all_65_at_real_beam_width(home: Path) -> None:
    # 65x1 at the real COMPOSE_BEAM_WIDTH=64 (no monkeypatch): a two-leg plan never beams, so all 65
    # journeys compose, no cut is disclosed, and no chain is mislabeled a lead.
    assert journeys.COMPOSE_BEAM_WIDTH == 64
    slug = make_trip(ROUND_TRIP)
    obs = []
    for i in range(65):
        cid = f"OB{i}"
        seed(cid, ob_detail(cid))
        obs.append(cand(cid, "SFO", "NRT", "2026-09-05"))
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug,
        obs,
        [cand("RET", "NRT", "SFO", "2026-09-12")],
        ret_states={"NRT": {"state": "complete"}},
    )
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    assert len(doc["journeys"]) == 65
    assert doc["unpaired_outbounds"] == []
    assert "truncation" not in doc["provenance"]


def test_lead_quota_floor_stops_after_cached_pair_composes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A/B: a cached outbound+return pair composes and is written, then an uncached lead outbound
    # (no bookable return) crosses the quota floor on its live expand — an honest, resumable stop.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    slug = make_trip(ROUND_TRIP)
    seed("OB1", ob_detail("OB1", dest="NRT"))  # cached: composes with the cached return below
    seed("RET", ret_detail("RET", origin="NRT"))
    connect(cache_db(), now=clock()).record_quota("/trips", 50)  # below the default floor of 100
    write_shortlists(
        slug,
        # OB2 -> ICN has no cached detail and no return from ICN: an unpaired lead, live-fetched
        [cand("OB1", "SFO", "NRT", "2026-09-05"), cand("OB2", "SFO", "ICN", "2026-09-05")],
        [cand("RET", "NRT", "SFO", "2026-09-12")],
        ret_states={"NRT": {"state": "complete"}},
    )
    with pytest.raises(QuotaFloorError):
        journeys.run(slug, now=clock())
    doc = json.loads(trips.artifact_read(slug, "expand.json"))
    assert [j["id"] for j in doc["journeys"]] == ["outbound:OB1:J|return:RET:J"]  # pair landed
    assert doc["provenance"]["quota_stopped"] is True
    assert doc["leg_states"]["outbound:OB2:J"] == {"state": "not_run", "reason": "quota_floor"}
    assert doc["unpaired_outbounds"] == []  # the breaking lead never reaches the leads list
    assert trips.phase_check(slug, "expand", now=clock())[1] is None  # unstamped to resume


def test_dateline_crossing_pair_composes_when_return_predates_departure_date(home: Path) -> None:
    # Eastbound over the dateline: the outbound arrives an earlier local date than it departs, so a
    # return departing after that arrival clock must pair though its date precedes the outbound's.
    slug = make_trip(ROUND_TRIP)
    seed("OB", detail("OB", [seg("SYD", "LAX", "2026-09-05T00:30", "2026-09-04T20:00")]))
    seed("RET", detail("RET", [seg("LAX", "SYD", "2026-09-04T23:00", "2026-09-06T08:00")]))
    write_shortlists(
        slug,
        [cand("OB", "SYD", "LAX", "2026-09-05")],
        [cand("RET", "LAX", "SYD", "2026-09-04")],
        ret_states={"LAX": {"state": "complete"}},
    )
    doc = journeys.run(slug, now=clock()) and json.loads(trips.artifact_read(slug, "expand.json"))
    (journey,) = doc["journeys"]
    assert journey["id"] == "outbound:OB:J|return:RET:J"
    assert doc["unpaired_outbounds"] == []


@pytest.mark.parametrize(
    ("mode", "position", "cash_variant_kind"),
    [
        pytest.param("cash", "first", "cash→award→award", id="cash-first-positioning"),
        pytest.param("cash", "last", "award→award→cash", id="cash-last-home-hop"),
        pytest.param("either", "first", "cash→award→award", id="either-first"),
        pytest.param("either", "last", "award→award→cash", id="either-last"),
    ],
)
def test_cash_or_either_end_leg_composes_through_run(
    home: Path, mode: str, position: str, cash_variant_kind: str
) -> None:
    # A cash/either leg in first (positioning) or last ($origins home) position composes through
    # journeys.run — fit reads its quote clocks, never crashing on a missing award detail.
    legs = [
        {"id": "outbound", "origins": ["SFO"], "dests": ["NRT"]},
        {"id": "mid", "dests": ["OKA"]},
        {"id": "return", "dests": "$origins"},
    ]
    idx = {"first": 0, "last": 2}[position]
    legs[idx] = {**legs[idx], "mode": mode}
    slug = make_trip({"legs": legs})

    def write_leg(leg_id: str, o: str, d: str, date: str, dep: str, arr: str, cid: str) -> None:
        leg_mode = next(leg for leg in legs if leg["id"] == leg_id).get("mode", "award")
        if leg_mode in ("award", "either"):
            seed(cid, detail(cid, [seg(o, d, dep, arr)]))
            trips.artifact_write(
                slug,
                f"legs/{leg_id}/shortlist.json",
                json.dumps(shortlist_doc([cand(cid, o, d, date)], leg=leg_id)),
            )
        if leg_mode in ("cash", "either"):
            trips.artifact_write(
                slug,
                f"legs/{leg_id}/bridge.json",
                json.dumps(
                    {
                        "quotes": [
                            cash_quote(o, d, 120.0, date=date, departs_local=dep, arrives_local=arr)
                        ],
                        "failures": [],
                    }
                ),
            )

    write_leg("outbound", "SFO", "NRT", "2026-09-05", "2026-09-05T10:00", "2026-09-06T14:00", "OB")
    write_leg("mid", "NRT", "OKA", "2026-09-08", "2026-09-08T09:00", "2026-09-08T12:00", "MID")
    write_leg("return", "OKA", "SFO", "2026-09-12", "2026-09-12T16:00", "2026-09-12T10:00", "RET")

    doc = _run(slug)
    assert doc["journeys"], "a cash/either end leg must compose, not raise KeyError"
    assert cash_variant_kind in {j["kind"] for j in doc["journeys"]}
