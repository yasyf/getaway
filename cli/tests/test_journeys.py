import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _api import api_row, shortlist_doc, sweep_envelope
from _api import seed as ingest_rows

from getaway import journeys, prefs, shortlist, sweeps, trips
from getaway.constants import COMPOSE_BEAM_WIDTH
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


def biz_row(rid: str, origin: str, dest: str, date: str = "2026-09-05") -> dict:
    return api_row(
        rid,
        origin,
        dest,
        date,
        "aeroplan",
        {"J": {"mileage": "80000", "seats": 2, "airlines": "UA", "direct": True}},
    )


def pipeline_shortlist(
    slug: str,
    leg: str,
    rows: list[dict],
    *,
    expanded_origins: list[str],
    states: dict | None = None,
    fetched_at: str = "2026-09-01T00:00:00+00:00",
) -> dict:
    """Drive the real sweep→shortlist path: ingest availability under the leg's sweep label, write
    the sweep artifact, run the production shortlist — so candidates and feasibility come from the
    pipeline, never hand-seeded. ``fetched_at`` sets the sweep's provenance age for TTL tests."""
    ingest_rows(slug, leg, "search", rows, clock())
    env = sweep_envelope(rows, expanded_origins=expanded_origins, search_states=states or {})
    env["provenance"]["fetched_at"] = fetched_at
    trips.artifact_write(slug, f"legs/{leg}/sweep.json", json.dumps(env))
    return shortlist.shortlist(slug, leg=leg, now=clock())


def sweep_states(dests: list[str], rows: list[dict] | None = None, *, field: str = "dest") -> dict:
    """Per-endpoint search states built through the sweep-side writer so fixtures carry
    production-keyed shapes and can't drift from what a real sweep writes (R-B)."""
    leg = {"endpoints": list(dests), "endpoint_field": field}
    return sweeps._search_states(leg, rows or [], has_more=False, stop=None)


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


def test_beam_caps_three_leg_composition_cheapest_first_and_discloses_cut(home: Path) -> None:
    # The beam bounds only ≥3-leg plans. Two full chains compose; a beam_width of 1 keeps the
    # cheaper one, the cut is disclosed as composition truncation, and no chain becomes a lead.
    slug = make_trip({**HYBRID_ROUND_TRIP, "tuning": {"beam_width": 1}})
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
    assert doc["gated"] == [{"journey_id": CASH_HOP_JID, "reason": "transits FUK, which you avoid"}]


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
            "id": "outbound",
            "origins": ["SFO"],
            "dests": ["NRT"],
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
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([cand("HOP", "NRT", "BKK", "2026-09-10")], leg="hop")),
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
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
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([cand("HOP", "NRT", "BKK", "2026-09-07")], leg="hop")),
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET", "BKK", "SFO", "2026-09-11")], leg="return")),
    )
    assert _run(slug)["journeys"] == []


def test_award_prior_stay_boundary_never_pre_filtered(home: Path) -> None:
    # The award outbound's stay is departure-proxy-infeasible (09-10 − 09-05 = 5 ≠ 4) but
    # arrival-feasible (09-10 − overnight arrival 09-06 = 4); an award prior is never pre-filtered.
    slug = make_trip(MULTI_CITY)
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    seed("HOP", detail("HOP", [seg("NRT", "BKK", "2026-09-10T10:00", "2026-09-10T14:00")]))
    seed("RET", detail("RET", [seg("BKK", "SFO", "2026-09-14T16:00", "2026-09-14T20:00")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([cand("HOP", "NRT", "BKK", "2026-09-10")], leg="hop")),
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET", "BKK", "SFO", "2026-09-14")], leg="return")),
    )
    doc = _run(slug)
    assert len(doc["journeys"]) == 1  # the award-prior chain survived the pre-beam filter
    assert "date_infeasible" not in doc["provenance"]  # no award-prior boundary was ever filtered


HYBRID_STAY_ROUND_TRIP = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA"], "mode": "either", "stay_nights": {"min": 4, "max": 7}},
        {"id": "return", "dests": "$origins"},
    ]
}


def test_cash_next_boundary_judged_by_its_quote_clock_not_its_date_field() -> None:
    # A quote may cross midnight (date 09-06, departs_local 09-07T00:30): judge by the clock.
    prior = {"kind": "cash", "dest": "OKA", "quote": {"arrives_local": "2026-09-03T18:00"}}
    nxt = {
        "kind": "cash",
        "origin": "OKA",
        "date": "2026-09-06",
        "quote": {"departs_local": "2026-09-07T00:30"},
    }
    stay_intent = {"stay_nights": {"min": 4, "max": 7}}
    assert not journeys._pool_boundary_date_infeasible(prior, nxt, stay_intent)


def test_date_infeasible_cash_hops_drop_pre_beam_so_the_feasible_chain_composes(home: Path) -> None:
    # A2: at beam_width 3 the three cheaper cash hops (each under the 4-night stay floor) would fill
    # the beam and leave 0 journeys; the filter drops them so the one feasible chain composes.
    slug = make_trip({**HYBRID_STAY_ROUND_TRIP, "tuning": {"beam_width": 3}})
    seed("GW-NRT", ob_detail("GW-NRT", dest="NRT"))  # SFO->NRT dep 09-05, arr 09-06T14:00
    seed(
        "RET-OKA",
        ret_detail("RET-OKA", origin="OKA", dep="2026-09-11T16:00", arr="2026-09-11T20:00"),
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET-OKA", "OKA", "SFO", "2026-09-11")], leg="return")),
    )
    feasible = cash_quote(
        "NRT",
        "OKA",
        200.0,  # dearer than the infeasibles, so only the filter keeps it in the beam
        date="2026-09-06",
        departs_local="2026-09-06T16:00",
        arrives_local="2026-09-06T18:00",  # arr 09-06 -> return 09-11 = 5 nights, in [4,7]
    )
    infeasible = [
        cash_quote(
            "NRT",
            "OKA",
            100.0,
            date=f"2026-09-{day:02d}",
            departs_local=f"2026-09-{day:02d}T16:00",
            arrives_local=f"2026-09-{day:02d}T18:00",  # -> return 09-11 = 3/2/1 nights, under floor
        )
        for day in (8, 9, 10)
    ]
    write_hybrid(slug, gateways=[gw_cand("GW-NRT", "NRT")], quotes=[feasible, *infeasible])

    doc = _run(slug)

    (journey,) = doc["journeys"]  # the one feasible chain composed
    assert journey["kind"] == "award→cash→award · 1 stay"
    assert journey["legs"][1]["cash"]["depart_date"] == "2026-09-06"  # the feasible hop
    assert doc["provenance"]["date_infeasible"] == 3  # the 3 cheaper hops proved infeasible
    assert "truncation" not in doc["provenance"]  # one feasible chain, so no beam cut


POSITIONING = {
    "legs": [
        {
            "id": "positioning",
            "origins": ["SFO"],
            "dests": ["LAX"],
            "mode": "cash",
            "optional": True,
            "role": "positioning",
        },
        {"id": "onward", "dests": ["NRT"]},
    ]
}


def test_positioning_cash_leg_composes_as_an_ordinary_chain_leg(home: Path) -> None:
    slug = make_trip(POSITIONING)
    seed("ONW", detail("ONW", [seg("LAX", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    trips.artifact_write(
        slug,
        "legs/positioning/bridge.json",
        json.dumps(
            {
                "quotes": [
                    cash_quote(
                        "SFO",
                        "LAX",
                        90.0,
                        date="2026-09-03",
                        departs_local="2026-09-03T08:00",
                        arrives_local="2026-09-03T11:00",
                    )
                ],
                "failures": [],
            }
        ),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
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


def _positioning_bridge(slug: str) -> None:
    trips.artifact_write(
        slug,
        "legs/positioning/bridge.json",
        json.dumps(
            {
                "quotes": [
                    cash_quote(
                        "SFO",
                        "LAX",
                        90.0,
                        date="2026-09-03",
                        departs_local="2026-09-03T08:00",
                        arrives_local="2026-09-03T11:00",
                    )
                ],
                "failures": [],
            }
        ),
    )


def test_optional_positioning_leg_yields_both_positioned_and_direct_variants(home: Path) -> None:
    # Regression ask #4, pipeline-produced (R-A): the onward sweep, transparent to the optional
    # positioning leg, lets the real shortlist emit both LAX->NRT and home-direct SFO->NRT.
    slug = make_trip(POSITIONING)
    sweep_on = next(n for n in trips.compile_graph(slug)["nodes"] if n["id"] == "sweep:onward")
    assert sweep_on["endpoint_source"]["skip_sources"] == [{"union": ["SFO"]}]  # covers home
    rows = [biz_row("ONW-LAX", "LAX", "NRT"), biz_row("ONW-SFO", "SFO", "NRT")]
    sl = pipeline_shortlist(slug, "onward", rows, expanded_origins=["LAX", "SFO"])
    assert {c["id"] for c in sl["candidates"]} == {"ONW-LAX", "ONW-SFO"}  # both from feasibility
    seed("ONW-LAX", detail("ONW-LAX", [seg("LAX", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    seed("ONW-SFO", detail("ONW-SFO", [seg("SFO", "NRT", "2026-09-05T09:00", "2026-09-06T13:00")]))
    _positioning_bridge(slug)
    doc = _run(slug)
    assert doc["gated"] == []
    by_id = {journey["id"]: journey for journey in doc["journeys"]}
    # The positioned variant carries the leg id segment; the skip variant simply lacks it.
    assert set(by_id) == {
        "positioning:cash:SFO:LAX:2026-09-03:economy|onward:ONW-LAX:J",
        "onward:ONW-SFO:J",
    }
    # the full variant never departs SFO — positioning's LAX landing anchors the onward leg
    assert "positioning:cash:SFO:LAX:2026-09-03:economy|onward:ONW-SFO:J" not in by_id
    positioned = by_id["positioning:cash:SFO:LAX:2026-09-03:economy|onward:ONW-LAX:J"]
    direct = by_id["onward:ONW-SFO:J"]
    assert positioned["kind"] == "cash→award"
    assert [(leg["role"], leg["mode"]) for leg in positioned["legs"]] == [
        ("positioning", "cash"),
        ("onward", "award"),
    ]
    assert direct["kind"] == "award"
    assert [(leg["role"], leg["mode"]) for leg in direct["legs"]] == [("onward", "award")]
    assert direct["legs"][0]["detail"]["segments"][0]["origin"] == "SFO"  # home-origin direct
    assert direct["cost"]["cash"] == []  # skip variant carries no positioning cash cost
    assert positioned["cost"]["cash"][0]["amount_cents"] == 9000


def test_optional_leg_skip_variant_absent_without_a_home_origin_candidate(home: Path) -> None:
    # Pipeline-produced: the onward sweep searches home (SFO) via skip transparency but finds no SFO
    # availability, so feasibility yields only the LAX candidate — one journey, no skip variant.
    slug = make_trip(POSITIONING)
    sl = pipeline_shortlist(
        slug, "onward", [biz_row("ONW", "LAX", "NRT")], expanded_origins=["LAX"]
    )
    assert {c["id"] for c in sl["candidates"]} == {"ONW"}
    seed("ONW", detail("ONW", [seg("LAX", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    _positioning_bridge(slug)
    doc = _run(slug)
    assert [journey["id"] for journey in doc["journeys"]] == [
        "positioning:cash:SFO:LAX:2026-09-03:economy|onward:ONW:J"
    ]
    assert "leads" not in doc


def test_optional_positioned_variant_starvation_disclosed_in_provenance(home: Path) -> None:
    # R-M: the positioned variant's onward padding row departs LAX before the cash positioning
    # lands, so it expands then dies at continuity — per-variant provenance keeps that honest.
    slug = make_trip(POSITIONING)
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(
            shortlist_doc(
                [
                    cand("ONW-LAX", "LAX", "NRT", "2026-08-31"),
                    cand("ONW-SFO", "SFO", "NRT", "2026-09-05"),
                ],
                leg="onward",
            )
        ),
    )
    seed("ONW-LAX", detail("ONW-LAX", [seg("LAX", "NRT", "2026-08-31T10:00", "2026-09-01T14:00")]))
    seed("ONW-SFO", detail("ONW-SFO", [seg("SFO", "NRT", "2026-09-05T09:00", "2026-09-06T13:00")]))
    _positioning_bridge(slug)

    doc = _run(slug)

    assert [j["id"] for j in doc["journeys"]] == ["onward:ONW-SFO:J"]  # only the direct variant
    variants = doc["provenance"]["variants"]
    assert variants["positioning+onward"] == {
        "chains_built": 1,
        "chains_expanded": 1,
        "dropped_continuity": 1,
        "journeys": 0,
    }
    assert variants["onward"] == {
        "chains_built": 1,
        "chains_expanded": 1,
        "dropped_continuity": 0,
        "journeys": 1,
    }


def test_no_optional_plan_has_no_variants_provenance_key(home: Path) -> None:
    # R-M degeneracy: a plan without optional legs keeps byte-identical provenance, no variants key.
    slug = make_trip(ONE_WAY)
    seed("OB", ob_detail("OB"))
    write_shortlists(slug, [cand("OB", "SFO", "NRT", "2026-09-05")])

    doc = _run(slug)

    assert len(doc["journeys"]) == 1
    assert "variants" not in doc["provenance"]


POSITIONING_OPEN_JAW = {
    "legs": [
        {
            "id": "positioning",
            "origins": ["SFO"],
            "dests": ["LAX"],
            "mode": "cash",
            "optional": True,
        },
        {"id": "onward", "origins": ["LAX"], "dests": ["NRT"]},
    ]
}


def test_skip_variant_first_leg_explicit_origins_replace_the_home_filter(home: Path) -> None:
    # R-A precedence: the skip variant's new opening leg declares its own origins (open jaw), which
    # REPLACE the home filter — so it departs LAX standalone, not filtered to home SFO.
    slug = make_trip(POSITIONING_OPEN_JAW)
    sweep_on = next(n for n in trips.compile_graph(slug)["nodes"] if n["id"] == "sweep:onward")
    assert sweep_on["endpoint_source"]["override"] == {"origins": ["LAX"], "dests": ["NRT"]}
    sl = pipeline_shortlist(
        slug, "onward", [biz_row("ONW", "LAX", "NRT")], expanded_origins=["LAX"]
    )
    assert {c["id"] for c in sl["candidates"]} == {"ONW"}
    seed("ONW", detail("ONW", [seg("LAX", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    _positioning_bridge(slug)
    doc = _run(slug)
    # both variants depart LAX per the onward leg's own declaration — uniform across variants
    assert {journey["id"] for journey in doc["journeys"]} == {
        "positioning:cash:SFO:LAX:2026-09-03:economy|onward:ONW:J",
        "onward:ONW:J",
    }


def _write_multi_city_prefix(
    slug: str, *, hop_states: dict, hop_fetched_at: str = "2026-09-01T00:00:00+00:00"
) -> None:
    """The outbound composes through the real pipeline; the hop market is dead (empty shortlist),
    its per-dest states built via the sweep writer (R-B). The return is an unreached beyond-leg."""
    pipeline_shortlist(slug, "outbound", [biz_row("OB", "SFO", "NRT")], expanded_origins=["SFO"])
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    pipeline_shortlist(
        slug, "hop", [], expanded_origins=[], states=hop_states, fetched_at=hop_fetched_at
    )
    trips.artifact_write(
        slug, "legs/return/shortlist.json", json.dumps(shortlist_doc([], leg="return"))
    )


def test_three_leg_dead_middle_surfaces_prefix_lead_with_honest_state(home: Path) -> None:
    # No hop availability ⇒ no full chain; the outbound prefix (a stay point) surfaces as a lead
    # carrying the hop's honest per-dest state (R-B: keyed by BKK, the leg's own endpoint).
    slug = make_trip(MULTI_CITY)
    _write_multi_city_prefix(slug, hop_states=sweep_states(["BKK"]))
    doc = _run(slug)
    assert doc["journeys"] == []
    assert doc["unpaired_outbounds"] == []
    (lead,) = doc["leads"]
    assert [entry["leg_id"] for entry in lead["prefix"]] == ["outbound"]
    assert lead["prefix"][0]["dest"] == "NRT"
    assert lead["reached"] == "NRT"
    remaining = {entry["leg_id"]: entry["search_state"] for entry in lead["remaining"]}
    assert remaining["hop"] == {"BKK": {"state": "searched_empty"}}  # dest-keyed per the sweep
    assert remaining["return"] == {"state": "not_run", "reason": "prefix_incomplete"}


def test_dead_middle_prefix_lead_reports_not_run_when_hop_unsearched(home: Path) -> None:
    # An unsearched hop reads not_run/no_search per its own dest, never a false "searched, empty".
    slug = make_trip(MULTI_CITY)
    _write_multi_city_prefix(slug, hop_states={})
    doc = _run(slug)
    (lead,) = doc["leads"]
    remaining = {entry["leg_id"]: entry["search_state"] for entry in lead["remaining"]}
    assert remaining["hop"] == {"BKK": {"state": "not_run", "reason": "no_search"}}


def test_dead_middle_prefix_lead_downgrades_expired_searched_empty(home: Path) -> None:
    # R-C: a searched-empty hop whose sweep TTL has lapsed downgrades to unverified, exactly as the
    # two-leg unpaired path does — a stale empty market never reads as authoritatively dead.
    slug = make_trip(MULTI_CITY)
    _write_multi_city_prefix(
        slug, hop_states=sweep_states(["BKK"]), hop_fetched_at="2026-07-13T12:00:00+00:00"
    )
    doc = _run(slug)
    (lead,) = doc["leads"]
    remaining = {entry["leg_id"]: entry["search_state"] for entry in lead["remaining"]}
    assert remaining["hop"] == {"BKK": {"state": "searched_empty", "verification": "unverified"}}


BUCKETED_MIDDLE = {
    "legs": [
        {
            "id": "outbound",
            "origins": ["SFO"],
            "dests": ["NRT"],
            "stay_nights": {"min": 4, "max": 4},
        },
        {
            "id": "hop",
            "buckets": [{"name": "asia", "dests": ["BKK"]}],
            "stay_nights": {"min": 4, "max": 4},
        },
        {"id": "return", "dests": "$origins"},
    ]
}
PROGRAM_SWEEP_MIDDLE = {
    "legs": [
        {
            "id": "outbound",
            "origins": ["SFO"],
            "dests": ["NRT"],
            "stay_nights": {"min": 4, "max": 4},
        },
        {
            "id": "hop",
            "program_sweeps": [{"source": "aeroplan", "dest_region": "Asia"}],
            "stay_nights": {"min": 4, "max": 4},
        },
        {"id": "return", "dests": "$origins"},
    ]
}


def test_bucketed_dead_middle_lead_reads_labeled_sweep_provenance(home: Path) -> None:
    # MINOR-1: a bucketed hop writes only sweep-asia.json, never the bare sweep.json — the lead's
    # provenance aggregates the labeled sweep names, so a stale dead market downgrades honestly.
    slug = make_trip(BUCKETED_MIDDLE)
    pipeline_shortlist(slug, "outbound", [biz_row("OB", "SFO", "NRT")], expanded_origins=["SFO"])
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    env = sweep_envelope([], expanded_origins=[], search_states=sweep_states(["BKK"]))
    env["provenance"]["fetched_at"] = "2026-07-13T12:00:00+00:00"  # >24h stale vs FROZEN 09-01
    trips.artifact_write(slug, "legs/hop/sweep-asia.json", json.dumps(env))
    shortlist.shortlist(slug, leg="hop", now=clock())
    trips.artifact_write(
        slug, "legs/return/shortlist.json", json.dumps(shortlist_doc([], leg="return"))
    )
    doc = _run(slug)
    (lead,) = doc["leads"]
    hop = next(entry for entry in lead["remaining"] if entry["leg_id"] == "hop")
    assert hop["searched_at"] == "2026-07-13T12:00:00+00:00"  # read from sweep-asia.json, not null
    assert hop["cache_age_hours"] is not None
    assert hop["search_state"] == {"BKK": {"state": "searched_empty", "verification": "unverified"}}


def test_program_sweeps_dead_middle_lead_keys_state_by_sweep_label(home: Path) -> None:
    # MINOR-2: a program-sweeps-only hop forwards no concrete dests; its lead state keys by the
    # sweep LABEL its states were written under (aeroplan-asia), never an empty {} reading nothing.
    slug = make_trip(PROGRAM_SWEEP_MIDDLE)
    pipeline_shortlist(slug, "outbound", [biz_row("OB", "SFO", "NRT")], expanded_origins=["SFO"])
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    states = sweeps._search_states(
        {"endpoints": ["aeroplan-asia"], "endpoint_field": None}, [], has_more=False, stop=None
    )
    env = sweep_envelope([], expanded_origins=[], search_states=states)
    env["provenance"]["fetched_at"] = "2026-09-01T00:00:00+00:00"  # fresh: no TTL downgrade
    trips.artifact_write(slug, "legs/hop/sweep-aeroplan-asia.json", json.dumps(env))
    shortlist.shortlist(slug, leg="hop", now=clock())
    trips.artifact_write(
        slug, "legs/return/shortlist.json", json.dumps(shortlist_doc([], leg="return"))
    )
    doc = _run(slug)
    (lead,) = doc["leads"]
    hop = next(entry for entry in lead["remaining"] if entry["leg_id"] == "hop")
    assert hop["search_state"] == {"aeroplan-asia": {"state": "searched_empty"}}


MIXED_MIDDLE = {
    "legs": [
        {
            "id": "outbound",
            "origins": ["SFO"],
            "dests": ["NRT"],
            "stay_nights": {"min": 4, "max": 4},
        },
        {
            "id": "hop",
            "buckets": [{"name": "asia", "dests": ["BKK"]}],
            "program_sweeps": [{"source": "aeroplan", "dest_region": "Asia"}],
            "stay_nights": {"min": 4, "max": 4},
        },
        {"id": "return", "dests": "$origins"},
    ]
}


def test_mixed_bucket_and_program_dead_middle_lead_keeps_both_state_keys(home: Path) -> None:
    # MINOR (R-B): a hop with BOTH buckets and program_sweeps keys its lead state ADDITIVELY — the
    # bucket's dest (BKK) AND the region sweep's label (aeroplan-asia), never dropping the label.
    slug = make_trip(MIXED_MIDDLE)
    pipeline_shortlist(slug, "outbound", [biz_row("OB", "SFO", "NRT")], expanded_origins=["SFO"])
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    bucket = sweep_envelope([], expanded_origins=[], search_states=sweep_states(["BKK"]))
    bucket["provenance"]["fetched_at"] = "2026-09-01T00:00:00+00:00"  # fresh: no TTL downgrade
    trips.artifact_write(slug, "legs/hop/sweep-asia.json", json.dumps(bucket))
    program_states = sweeps._search_states(
        {"endpoints": ["aeroplan-asia"], "endpoint_field": None}, [], has_more=False, stop=None
    )
    program = sweep_envelope([], expanded_origins=[], search_states=program_states)
    program["provenance"]["fetched_at"] = "2026-09-01T00:00:00+00:00"
    trips.artifact_write(slug, "legs/hop/sweep-aeroplan-asia.json", json.dumps(program))
    shortlist.shortlist(slug, leg="hop", now=clock())
    trips.artifact_write(
        slug, "legs/return/shortlist.json", json.dumps(shortlist_doc([], leg="return"))
    )
    doc = _run(slug)
    (lead,) = doc["leads"]
    hop = next(entry for entry in lead["remaining"] if entry["leg_id"] == "hop")
    assert hop["search_state"] == {
        "BKK": {"state": "searched_empty"},
        "aeroplan-asia": {"state": "searched_empty"},
    }


RETURN_ONLY_OPTIONAL = {
    "legs": [
        {
            "id": "positioning",
            "origins": ["SFO"],
            "dests": ["LAX"],
            "mode": "cash",
            "optional": True,
        },
        {"id": "return", "dests": "$origins"},
    ]
}


def test_leg_variants_excludes_a_variant_whose_first_leg_targets_origins() -> None:
    # R-D: skipping the only non-$origins leg leaves a fly-home-from-home shape — excluded, same
    # class as the dropped empty variant. The full variant survives.
    legs = [
        {"id": "positioning", "dests": ["LAX"], "optional": True},
        {"id": "return", "dests": "$origins"},
    ]
    assert journeys._leg_variants(legs) == [[0, 1]]


def test_all_optional_before_home_never_crashes_on_a_home_departing_return(home: Path) -> None:
    # R-D repro (cc-notes log 0e780d2): a home-origin return candidate would drive the return-only
    # variant into fit with one typed leg (IndexError at fit.py:194). The variant is excluded.
    slug = make_trip(RETURN_ONLY_OPTIONAL)
    seed(
        "RET-HOME", detail("RET-HOME", [seg("SFO", "SFO", "2026-09-12T16:00", "2026-09-12T20:00")])
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET-HOME", "SFO", "SFO", "2026-09-12")], leg="return")),
    )
    _positioning_bridge(slug)
    doc = _run(slug)  # no IndexError
    assert doc["journeys"] == []  # the degenerate fly-home-from-home variant never composes


def test_full_chain_success_produces_no_partial_leads(home: Path) -> None:
    # A full three-leg chain composes ⇒ no prefix surfaces as a lead.
    slug = make_trip(MULTI_CITY)
    seed("OB", detail("OB", [seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")]))
    seed("HOP", detail("HOP", [seg("NRT", "BKK", "2026-09-10T10:00", "2026-09-10T14:00")]))
    seed("RET", detail("RET", [seg("BKK", "SFO", "2026-09-14T16:00", "2026-09-14T20:00")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/hop/shortlist.json",
        json.dumps(shortlist_doc([cand("HOP", "NRT", "BKK", "2026-09-10")], leg="hop")),
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET", "BKK", "SFO", "2026-09-14")], leg="return")),
    )
    doc = _run(slug)
    assert len(doc["journeys"]) == 1
    assert "leads" not in doc


# --- degeneracy gate: two-leg exhaustive vs ≥3-leg beam, and dateline continuity -----------------


def test_two_leg_round_trip_composes_exhaustively_ignoring_beam(home: Path) -> None:
    # The beam bounds only ≥3-leg plans: even with plan.tuning.beam_width 2, a two-leg round trip
    # pairs all 9x8 = 72, no cut, no leads.
    slug = make_trip({**ROUND_TRIP, "tuning": {"beam_width": 2}})
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
    # 65x1 at the default beam width 64 (no tuning): a two-leg plan never beams, so all 65 journeys
    # compose, no cut is disclosed, and no chain is mislabeled a lead.
    assert COMPOSE_BEAM_WIDTH == 64
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


# --- T3: manual chains (legs/manual.json) — the agent invents the shape, the CLI prices it -------

OPEN_JAW = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA"], "mode": "award"},
    ]
}
CASH_ONWARD = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA"], "mode": "cash"},
    ]
}


def write_manual(slug: str, chains: list) -> None:
    trips.artifact_write(slug, "legs/manual.json", json.dumps(chains))


def test_manual_absent_leaves_expand_byte_identical(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug, [cand("OB", "SFO", "NRT", "2026-09-05")], [cand("RET", "NRT", "SFO", "2026-09-12")]
    )
    doc = _run(slug)
    assert "manual_rejected" not in doc
    assert all("provenance" not in journey for journey in doc["journeys"])


def test_manual_chain_dedupes_and_prices_identically_to_composed(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug, [cand("OB", "SFO", "NRT", "2026-09-05")], [cand("RET", "NRT", "SFO", "2026-09-12")]
    )
    baseline = _run(slug)["journeys"]
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "return", "candidate": "RET"}]],
    )
    doc = _run(slug)
    assert doc["journeys"] == baseline  # composed wins the dedup, byte-for-byte — no manual boost
    assert "manual_rejected" not in doc
    (journey,) = doc["journeys"]
    assert "provenance" not in journey


def test_manual_only_cross_airport_chain_prices_and_badges_provenance(home: Path) -> None:
    # A surface hop between airports the topology superset never anchors (NRT arrival, HND
    # departure): composition alone yields nothing, but the agent's explicit chain prices.
    slug = make_trip(OPEN_JAW)
    seed("OB", ob_detail("OB"))
    seed("ON", detail("ON", [seg("HND", "OKA", "2026-09-08T09:00", "2026-09-08T12:00", cabin="Y")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(
            shortlist_doc([cand("ON", "HND", "OKA", "2026-09-08", cabin="Y")], leg="onward")
        ),
    )
    assert _run(slug)["journeys"] == []  # composition never chains HND onto an NRT arrival
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "onward", "candidate": "ON"}]],
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["id"] == "outbound:OB:J|onward:ON:Y"
    assert journey["kind"] == "award→award"
    assert journey["provenance"] == "manual"
    assert "manual_rejected" not in doc


def test_manual_duplicate_chains_dedupe_to_one_journey(home: Path) -> None:
    slug = make_trip(OPEN_JAW)
    seed("OB", ob_detail("OB"))
    seed("ON", detail("ON", [seg("HND", "OKA", "2026-09-08T09:00", "2026-09-08T12:00", cabin="Y")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(
            shortlist_doc([cand("ON", "HND", "OKA", "2026-09-08", cabin="Y")], leg="onward")
        ),
    )
    chain = [{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "onward", "candidate": "ON"}]
    write_manual(slug, [chain, chain])
    assert len(_run(slug)["journeys"]) == 1


def test_manual_cash_leg_chain_prices_with_provenance(home: Path) -> None:
    slug = make_trip(CASH_ONWARD)
    seed("OB", ob_detail("OB"))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    quote = cash_quote("HND", "OKA", 240.0, date="2026-09-08")
    trips.artifact_write(
        slug, "legs/onward/bridge.json", json.dumps({"quotes": [quote], "failures": []})
    )
    write_manual(
        slug,
        [
            [
                {"leg_id": "outbound", "candidate": "OB"},
                {
                    "leg_id": "onward",
                    "candidate": {"gateway": "HND", "onward_dest": "OKA", "date": "2026-09-08"},
                },
            ]
        ],
    )
    doc = _run(slug)
    (journey,) = doc["journeys"]
    assert journey["kind"] == "award→cash"
    assert journey["provenance"] == "manual"
    assert journey["cost"]["cash"][0]["amount_cents"] == 24000


def test_manual_continuity_violation_lands_in_manual_rejected(home: Path) -> None:
    slug = make_trip(OPEN_JAW)
    seed("OB", ob_detail("OB"))  # arrives NRT 2026-09-06
    seed("ON", detail("ON", [seg("HND", "OKA", "2026-09-04T09:00", "2026-09-04T12:00", cabin="Y")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(
            shortlist_doc([cand("ON", "HND", "OKA", "2026-09-04", cabin="Y")], leg="onward")
        ),
    )
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "onward", "candidate": "ON"}]],
    )
    doc = _run(slug)
    assert doc["journeys"] == []
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == [
        {"leg_id": "outbound", "candidate": "OB"},
        {"leg_id": "onward", "candidate": "ON"},
    ]
    assert "does not continue" in rejected["reason"]


def test_manual_candidate_aged_out_of_shortlist_is_disclosed(home: Path) -> None:
    slug = make_trip(ROUND_TRIP)
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug,
        [cand("OB", "SFO", "NRT", "2026-09-05"), cand("OB2", "SFO", "NRT", "2026-09-07")],
        [cand("RET", "NRT", "SFO", "2026-09-12")],
    )
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB2"}, {"leg_id": "return", "candidate": "RET"}]],
    )
    # Rewrite the outbound shortlist without OB2 — a stale manual reference that no longer resolves.
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == [
        {"leg_id": "outbound", "candidate": "OB2"},
        {"leg_id": "return", "candidate": "RET"},
    ]
    assert "no longer available" in rejected["reason"]


GROWN = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA"], "mode": "award"},
        {"id": "return", "dests": "$origins"},
    ]
}
RENAMED = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "back", "dests": "$origins"},
    ]
}
STALE_CHAIN = [{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "return", "candidate": "RET"}]


def _seed_round_trip_manual(slug: str) -> None:
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET"))
    write_shortlists(
        slug, [cand("OB", "SFO", "NRT", "2026-09-05")], [cand("RET", "NRT", "SFO", "2026-09-12")]
    )
    write_manual(slug, [STALE_CHAIN])


def test_manual_chain_stale_after_plan_grows_lands_in_manual_rejected(home: Path) -> None:
    # A leg added under the input artifact: the chain no longer covers every plan leg, so it
    # re-validates to a coverage rejection — never a zip-truncated, role-misattributed journey.
    slug = make_trip(ROUND_TRIP)
    _seed_round_trip_manual(slug)
    trips.set_patch(slug, {"plan": GROWN})  # +onward; the artifact still lists only two legs
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == STALE_CHAIN
    assert "cover every plan leg once in order" in rejected["reason"]
    assert all(journey.get("provenance") != "manual" for journey in doc["journeys"])


def test_manual_chain_stale_after_leg_rename_lands_in_manual_rejected(home: Path) -> None:
    # A leg renamed under the artifact references an id no longer in the plan: it re-validates to a
    # manual_rejected reason naming the unknown id, not a raw KeyError.
    slug = make_trip(ROUND_TRIP)
    _seed_round_trip_manual(slug)
    trips.set_patch(slug, {"plan": RENAMED})  # 'return' → 'back'; the artifact still names 'return'
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == STALE_CHAIN
    assert rejected["reason"] == "leg 'return' is not a plan leg"
    assert all(journey.get("provenance") != "manual" for journey in doc["journeys"])


def test_manual_chain_duplicating_a_gated_composed_chain_dedupes_gated(home: Path) -> None:
    # A manual chain identical to a seat-gated composed chain gets the gated channel's id-dedupe
    # (first occurrence wins) — not a doubled disclosure.
    slug = make_trip(ROUND_TRIP, party=2)
    seed("OB", ob_detail("OB"))
    seed("RET", ret_detail("RET", seats=1))  # live return seats below the party of 2 → seat gate
    write_shortlists(
        slug,
        [cand("OB", "SFO", "NRT", "2026-09-05")],
        [cand("RET", "NRT", "SFO", "2026-09-12", seats=2)],
    )
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "return", "candidate": "RET"}]],
    )
    doc = _run(slug)
    assert doc["journeys"] == []
    (entry,) = doc["gated"]  # not doubled by the manual pass re-composing the same gated chain
    assert "below the party" in entry["reason"]


# R-F: a manual chain covers every MANDATORY leg in plan order, freely skipping OPTIONAL ones.

OPTIONAL_GROWN = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "onward", "dests": ["OKA"], "mode": "award", "optional": True},
        {"id": "return", "dests": "$origins"},
    ]
}
REORDERED = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "return", "dests": "$origins"},
        {"id": "onward", "dests": ["OKA"], "mode": "award"},
    ]
}
FULL_GROWN_CHAIN = [
    {"leg_id": "outbound", "candidate": "OB"},
    {"leg_id": "onward", "candidate": "ON"},
    {"leg_id": "return", "candidate": "RET"},
]


def _seed_grown_manual(slug: str) -> None:
    """A 3-leg SFO→NRT→OKA→SFO trip, every leg mandatory, its full manual chain written."""
    seed("OB", ob_detail("OB"))  # SFO→NRT
    seed("ON", detail("ON", [seg("NRT", "OKA", "2026-09-08T09:00", "2026-09-08T12:00")]))
    seed("RET", ret_detail("RET", origin="OKA"))  # OKA→SFO
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(shortlist_doc([cand("ON", "NRT", "OKA", "2026-09-08")], leg="onward")),
    )
    trips.artifact_write(
        slug,
        "legs/return/shortlist.json",
        json.dumps(shortlist_doc([cand("RET", "OKA", "SFO", "2026-09-12")], leg="return")),
    )
    write_manual(slug, [FULL_GROWN_CHAIN])


def test_manual_chain_grown_by_optional_leg_prices_as_skip_variant(home: Path) -> None:
    # An OPTIONAL leg inserted under a two-leg chain: it still covers both mandatory legs, so it now
    # PRICES as the skip variant (dedupes with the composed twin) instead of a coverage rejection.
    slug = make_trip(ROUND_TRIP)
    _seed_round_trip_manual(slug)  # STALE_CHAIN=[outbound, return] on the two-leg plan
    trips.set_patch(slug, {"plan": OPTIONAL_GROWN})  # +optional onward between the two legs
    doc = _run(slug)
    assert "manual_rejected" not in doc
    assert any(journey["id"] == "outbound:OB:J|return:RET:J" for journey in doc["journeys"])


def test_manual_chain_stale_after_leg_removal_lands_in_manual_rejected(home: Path) -> None:
    # A removed leg the chain still names re-validates to the unknown-id reason, never a KeyError.
    slug = make_trip(GROWN)
    _seed_grown_manual(slug)
    trips.set_patch(slug, {"plan": ROUND_TRIP})  # onward removed; the chain still names it
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == FULL_GROWN_CHAIN
    assert rejected["reason"] == "leg 'onward' is not a plan leg"
    assert all(journey.get("provenance") != "manual" for journey in doc["journeys"])


def test_manual_chain_stale_after_leg_reorder_lands_in_manual_rejected(home: Path) -> None:
    # Reordered mandatory legs: the chain is no longer a plan-order subsequence → coverage reason.
    slug = make_trip(GROWN)
    _seed_grown_manual(slug)
    trips.set_patch(slug, {"plan": REORDERED})  # [outbound, return, onward]
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == FULL_GROWN_CHAIN
    assert rejected["reason"] == (
        "chain must cover every plan leg once in order "
        "['outbound', 'return', 'onward'], got ['outbound', 'onward', 'return']"
    )


def test_manual_chain_optional_flag_flip_on_covered_leg_still_prices(home: Path) -> None:
    # Flipping a COVERED leg's optional flag leaves the chain's variant unchanged, so it keeps
    # pricing across the boundary the topology never anchors.
    slug = make_trip(OPEN_JAW)
    seed("OB", ob_detail("OB"))
    seed("ON", detail("ON", [seg("HND", "OKA", "2026-09-08T09:00", "2026-09-08T12:00")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(shortlist_doc([cand("ON", "HND", "OKA", "2026-09-08")], leg="onward")),
    )
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "onward", "candidate": "ON"}]],
    )
    flipped = {"legs": [OPEN_JAW["legs"][0], {**OPEN_JAW["legs"][1], "optional": True}]}
    trips.set_patch(slug, {"plan": flipped})
    doc = _run(slug)
    (journey,) = [j for j in doc["journeys"] if j.get("provenance") == "manual"]
    assert journey["id"] == "outbound:OB:J|onward:ON:J"
    assert "manual_rejected" not in doc


def test_manual_chain_mode_flip_award_to_cash_discloses_unavailable(home: Path) -> None:
    # Flipping a covered leg award→cash strands its award id: coverage passes, resolution misses.
    slug = make_trip(OPEN_JAW)
    seed("OB", ob_detail("OB"))
    seed("ON", detail("ON", [seg("NRT", "OKA", "2026-09-08T09:00", "2026-09-08T12:00", cabin="Y")]))
    trips.artifact_write(
        slug,
        "legs/outbound/shortlist.json",
        json.dumps(shortlist_doc([cand("OB", "SFO", "NRT", "2026-09-05")])),
    )
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(
            shortlist_doc([cand("ON", "NRT", "OKA", "2026-09-08", cabin="Y")], leg="onward")
        ),
    )
    write_manual(
        slug,
        [[{"leg_id": "outbound", "candidate": "OB"}, {"leg_id": "onward", "candidate": "ON"}]],
    )
    cash_onward = {
        "legs": [OPEN_JAW["legs"][0], {"id": "onward", "dests": ["OKA"], "mode": "cash"}]
    }
    trips.set_patch(slug, {"plan": cash_onward})  # onward now cash: the award id can't resolve
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["reason"] == "leg 'onward': award candidate 'ON' is no longer available"
    assert all(journey.get("provenance") != "manual" for journey in doc["journeys"])


POS_ONWARD = {
    "legs": [
        {"id": "pos", "origins": ["SFO"], "dests": ["NRT"], "mode": "award", "optional": True},
        {"id": "onward", "dests": ["OKA"], "mode": "award"},
    ]
}
POS_HOME = {
    "legs": [
        {"id": "pos", "origins": ["SFO"], "dests": ["NRT"], "mode": "award", "optional": True},
        {"id": "onward", "dests": "$origins"},
    ]
}


def test_manual_skip_chain_opening_on_flipped_homeward_leg_is_rejected(home: Path) -> None:
    # R-D: a plan edit leaves the chain's only covered leg flying home to $origins → honest
    # rejection, never the fit.py single-leg IndexError.
    slug = make_trip(POS_ONWARD)
    seed("ON", detail("ON", [seg("NRT", "OKA", "2026-09-08T09:00", "2026-09-08T12:00")]))
    trips.artifact_write(
        slug,
        "legs/onward/shortlist.json",
        json.dumps(shortlist_doc([cand("ON", "NRT", "OKA", "2026-09-08")], leg="onward")),
    )
    write_manual(slug, [[{"leg_id": "onward", "candidate": "ON"}]])  # skip the optional positioning
    trips.set_patch(slug, {"plan": POS_HOME})  # onward now flies home to $origins
    doc = _run(slug)
    (rejected,) = doc["manual_rejected"]
    assert rejected["chain"] == [{"leg_id": "onward", "candidate": "ON"}]
    assert "homeward leg 'onward'" in rejected["reason"]
    assert all(journey.get("provenance") != "manual" for journey in doc["journeys"])
