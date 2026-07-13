import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from getaway import factors, prefs, stays, trips
from getaway.paths import UsageError

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 7, 13)
SLUG = "2026-09-warm"
WINDOW = {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 4}

ROUND_TRIP_LODGING = {
    "trip_type": "round_trip",
    "origins": ["SFO"],
    "buckets": [{"name": "warm", "dests": ["CUN"]}],
    "lodging": {},
}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def _trip(plan: dict) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, {"cabin": "business", "party": 2, "window": WINDOW, "plan": plan})
    return SLUG


def _leg(role: str, origin: str, dest: str, *, arr: str, dep: str) -> dict:
    return {
        "role": role,
        "origin": origin,
        "dest": dest,
        "arrives_local": arr,
        "departs_local": dep,
    }


def journey(
    jid: str,
    *,
    dest: str = "CUN",
    ob_arr: str = "2026-09-10T14:00",
    ret_dep: str | None = "2026-09-14T10:00",
    ret_origin: str | None = None,
    origin: str = "SFO",
) -> dict:
    legs = [_leg("outbound", origin, dest, arr=ob_arr, dep="2026-09-10T00:00")]
    if ret_dep is not None:
        legs.append(_leg("return", ret_origin or dest, origin, arr="2026-09-14T23:00", dep=ret_dep))
    return {"id": jid, "fit_facts": {"legs": legs}}


def hybrid_journey(
    jid: str, *, gateway: str = "NRT", onward: str = "OKA", origin: str = "SFO"
) -> dict:
    """A hybrid whose outbound side is a gateway award leg + a cash onward leg before the return —
    the stay's destination is the onward leg's, never the gateway's."""
    legs = [
        _leg("outbound", origin, gateway, arr="2026-09-10T14:00", dep="2026-09-10T00:00"),
        _leg("onward", gateway, onward, arr="2026-09-10T18:00", dep="2026-09-10T16:00"),
        _leg("return", onward, origin, arr="2026-09-14T23:00", dep="2026-09-14T09:00"),
    ]
    return {"id": jid, "fit_facts": {"legs": legs}}


def rank_entry(jrny: dict) -> dict:
    return {"journey": jrny, "facts": {}, "verdicts": [], "cost_tier": 0}


def write_rank(slug: str, journeys: list[dict], *, notable: list[dict] | None = None) -> None:
    doc = {
        "ranked": [rank_entry(j) for j in journeys],
        "notable_stretches": [
            {**rank_entry(j), "why": "back Tuesday, but perfect"} for j in notable or []
        ],
        "dropped": [],
    }
    trips.artifact_write(slug, "rank.json", json.dumps(doc))


def offer(
    *, award_class: str = "standard", points: int | None = 30000, cents: float | None = 16846
) -> dict:
    return {
        "award_class": award_class,
        "check_in": "2026-09-10",
        "nights": 4,
        "award_points_per_night": points,
        "cash_per_night_cents": cents,
        "cents_per_point": 1.5,
    }


def room(*, program: str = "hyatt", offers: list[dict] | None = None) -> dict:
    return {
        "rooms_aero_id": "h-1",
        "program": program,
        "name": "Grand Hyatt Playa",
        "lat": 21.16,
        "lng": -86.85,
        "currency": "USD",
        "last_checked_at": "2026-07-13T11:00:00.123456Z",
        "stale": False,
        "offers": offers if offers is not None else [offer()],
    }


def stay_entry(
    *, rooms: list[dict] | None = None, session: str = "pro", night_clamped: bool = False
) -> dict:
    return {
        "interval": {"check_in": "2026-09-10", "check_out": "2026-09-14", "nights": 4},
        "destination": {
            "airport": "CUN",
            "query": "Cancún, Mexico",
            "center": {"lat": 21.16, "lng": -86.85},
            "viewport": {"sw_lat": 21.0, "sw_lng": -87.0, "ne_lat": 21.3, "ne_lng": -86.7},
        },
        "provenance": {
            "source": "rooms.aero",
            "session": session,
            "fetched_at": "2026-07-13T11:00:00+00:00",
            "search_url": "https://rooms.aero/search?city=Canc%C3%BAn&nights=4",
            "revalidation": {"total": 66, "successful": 50, "queued": 0},
            "night_clamped": night_clamped,
        },
        "rooms": rooms if rooms is not None else [room()],
        "search_state": "complete",
    }


def node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


# --- Compile-graph wiring (deliverable 3) --------------------------------------------------------


def test_one_way_lodging_without_checkout_compiles_no_stays_node(getaway_home: Path) -> None:
    graph = trips.compile_graph(_trip({**ROUND_TRIP_LODGING, "trip_type": "one_way"}))
    assert "stays" not in [n["id"] for n in graph["nodes"]]
    assert graph["requires"] == []  # no checkout to derive → no session need
    assert "stays.json" not in node(graph, "finalize")["inputs"]


def test_one_way_lodging_with_explicit_checkout_compiles_stays_node(getaway_home: Path) -> None:
    plan = {**ROUND_TRIP_LODGING, "trip_type": "one_way", "lodging": {"checkout": "2026-09-20"}}
    graph = trips.compile_graph(_trip(plan))
    assert graph["requires"] == ["rooms_session"]
    assert node(graph, "stays")["outputs"] == ["stays.json"]


def test_stays_node_is_agent_shaped_with_helper_steps(getaway_home: Path) -> None:
    graph = trips.compile_graph(_trip(ROUND_TRIP_LODGING))
    stays_node = node(graph, "stays")
    assert stays_node["command"] is None  # the walk is agent-shaped, like assess
    assert stays_node["routing"] == {"model": "opus", "effort": "xhigh"}
    assert [s["command"] for s in stays_node["steps"]] == [
        ["getaway", "stays", "intervals", SLUG],
        ["getaway", "stays", "ingest", SLUG],
    ]


def test_stays_node_spends_zero_quota_and_is_absent_from_the_budget(getaway_home: Path) -> None:
    graph = trips.compile_graph(_trip(ROUND_TRIP_LODGING))
    assert node(graph, "stays")["quota_cost"] == 0
    assert "stays" not in [n["id"] for n in graph["quota_budget"]["nodes"]]


# --- Interval derivation (deliverable 2) ---------------------------------------------------------


def test_round_trip_interval_from_paired_timestamps(getaway_home: Path) -> None:
    result = stays.derive_interval(journey("J"), {"lodging": {}}, TODAY)
    assert result == {
        "disposition": "walk",
        "destination_airport": "CUN",
        "interval": {
            "check_in": "2026-09-10",
            "check_out": "2026-09-14",
            "nights": 4,
            "night_clamped": False,
        },
    }


@pytest.mark.parametrize(
    ("ret_dep", "expected_nights"),
    [
        pytest.param("2026-09-14T10:00", 4, id="monday-return"),
        pytest.param("2026-09-15T10:00", 5, id="tuesday-return-adds-a-night"),
    ],
)
def test_return_day_sets_the_night_count(
    getaway_home: Path, ret_dep: str, expected_nights: int
) -> None:
    result = stays.derive_interval(journey("J", ret_dep=ret_dep), {"lodging": {}}, TODAY)
    assert result["interval"]["nights"] == expected_nights
    assert result["interval"]["check_out"] == ret_dep[:10]


def test_stays_over_five_nights_clamp_to_five_and_disclose(getaway_home: Path) -> None:
    result = stays.derive_interval(journey("J", ret_dep="2026-09-20T10:00"), {"lodging": {}}, TODAY)
    assert result["interval"]["nights"] == 5  # rooms.aero hard block cap
    assert result["interval"]["check_out"] == "2026-09-15"  # check_in + 5
    assert result["interval"]["night_clamped"] is True


def test_check_in_before_today_defers_date_in_past(getaway_home: Path) -> None:
    past = journey("J", ob_arr="2026-07-01T14:00", ret_dep="2026-07-05T10:00")
    result = stays.derive_interval(past, {"lodging": {}}, TODAY)
    assert result["disposition"] == "deferred"
    assert result["reason"] == "date_in_past"
    assert result["check_in"] == "2026-07-01"


def test_hybrid_stay_uses_the_onward_leg_not_the_gateway(getaway_home: Path) -> None:
    # A gateway award + cash onward before the return: the stay sits at the onward destination.
    result = stays.derive_interval(
        hybrid_journey("H", gateway="NRT", onward="OKA"), {"lodging": {}}, TODAY
    )
    assert result["disposition"] == "walk"
    assert result["destination_airport"] == "OKA"  # onward_dest, never the gateway NRT
    assert result["interval"]["check_in"] == "2026-09-10"  # onward leg arrival, not the gateway's
    assert result["interval"]["check_out"] == "2026-09-14"


def test_cash_onward_without_observed_arrival_defers_unknown_arrival(getaway_home: Path) -> None:
    # A cash hop whose fit fact carries no arrival clock: check-in is unknowable, so lodging defers
    # (never a guessed check-in) rather than crashing on the missing key.
    j = hybrid_journey("H", gateway="NRT", onward="OKA")
    del j["fit_facts"]["legs"][1]["arrives_local"]  # the onward cash leg loses its arrival clock
    result = stays.derive_interval(j, {"lodging": {}}, TODAY)
    assert result == {
        "disposition": "deferred",
        "reason": "unknown_arrival",
        "destination_airport": "OKA",
    }


def test_open_jaw_without_checkout_defers_no_checkout(getaway_home: Path) -> None:
    # Outbound lands CUN, return departs a different city — no surface itinerary, no checkout.
    open_jaw = journey("J", dest="CUN", ret_origin="MEX")
    result = stays.derive_interval(open_jaw, {"lodging": {}}, TODAY)
    assert result == {
        "disposition": "deferred",
        "reason": "no_checkout",
        "destination_airport": "CUN",
    }


def test_one_way_without_checkout_defers_no_checkout(getaway_home: Path) -> None:
    result = stays.derive_interval(journey("J", ret_dep=None), {"lodging": {}}, TODAY)
    assert result == {
        "disposition": "deferred",
        "reason": "no_checkout",
        "destination_airport": "CUN",
    }


def test_explicit_checkout_overrides_a_missing_return(getaway_home: Path) -> None:
    result = stays.derive_interval(
        journey("J", ret_dep=None), {"lodging": {"checkout": "2026-09-13"}}, TODAY
    )
    assert result["disposition"] == "walk"
    assert result["interval"] == {
        "check_in": "2026-09-10",
        "check_out": "2026-09-13",
        "nights": 3,
        "night_clamped": False,
    }


# --- stays intervals command ---------------------------------------------------------------------


def test_intervals_worklist_covers_the_board_and_carries_a_fingerprint(getaway_home: Path) -> None:
    slug = _trip(ROUND_TRIP_LODGING)
    ranked = [journey(f"J{i}") for i in range(8)]
    write_rank(slug, ranked, notable=[journey("LATE", ret_dep="2026-09-15T10:00")])
    result = stays.intervals(slug, now=clock())
    ids = [w["journey_id"] for w in result["journeys"]]
    assert ids == [f"J{i}" for i in range(6)] + ["LATE"]  # cut of 6 plus the notable stretch
    assert result["inputs_fp"] == trips.capture_inputs_fp(trips.show(slug), prefs.show(), "stays")
    walk = result["journeys"][0]
    assert walk["disposition"] == "walk"
    assert walk["search_key"] == "CUN|2026-09-10|4"


def test_intervals_marks_open_jaw_deferred(getaway_home: Path) -> None:
    slug = _trip(ROUND_TRIP_LODGING)
    write_rank(slug, [journey("OJ", ret_origin="MEX")])
    (entry,) = stays.intervals(slug, now=clock())["journeys"]
    assert entry["disposition"] == "deferred"
    assert entry["interval"] is None
    assert entry["lodging_search"] == {"state": "deferred", "reason": "no_checkout"}


# --- stays ingest (deliverable 1) ----------------------------------------------------------------


def _ingest(slug: str, entries: dict, *, inputs_fp: str | None = None) -> dict:
    return stays.ingest(slug, json.dumps({"stays": entries}), inputs_fp=inputs_fp, now=clock())


@pytest.fixture
def ingestable(getaway_home: Path) -> str:
    slug = _trip(ROUND_TRIP_LODGING)
    write_rank(slug, [journey("J-CUN")])  # the stays node's declared input
    return slug


def test_ingest_writes_journey_namespaced_stays_and_stamps_the_node(ingestable: str) -> None:
    result = _ingest(ingestable, {"J-CUN": stay_entry()})
    assert result == {"journeys": 1, "rooms": 1}
    doc = json.loads(trips.artifact_read(ingestable, "stays.json"))
    assert set(doc["stays"]) == {"J-CUN"}
    assert doc["stays"]["J-CUN"]["provenance"]["session"] == "pro"
    assert doc["generated_at"] == FROZEN.isoformat()
    assert trips.phase_fresh(ingestable, "stays", now=clock())


def test_ingest_namespaces_multiple_journeys(ingestable: str) -> None:
    write_rank(ingestable, [journey("J-CUN"), journey("J-CUN-2")])
    _ingest(ingestable, {"J-CUN": stay_entry(), "J-CUN-2": stay_entry(rooms=[])})
    doc = json.loads(trips.artifact_read(ingestable, "stays.json"))
    assert set(doc["stays"]) == {"J-CUN", "J-CUN-2"}


def test_ingest_forwards_inputs_fp_for_freshness(ingestable: str) -> None:
    fp = stays.intervals(ingestable, now=clock())["inputs_fp"]
    _ingest(ingestable, {"J-CUN": stay_entry()}, inputs_fp=fp)
    _, record = trips.phase_check(ingestable, "stays", now=clock())
    assert record is not None
    assert record["inputs_fp"] == fp


def test_ingest_rejects_unknown_program_slug_naming_the_received_slug(ingestable: str) -> None:
    entries = {"J-CUN": stay_entry(rooms=[room(program="wyndham-rewards")])}
    with pytest.raises(UsageError, match="wyndham-rewards"):
        _ingest(ingestable, entries)


@pytest.mark.parametrize(
    "slug", sorted({"hyatt", "hilton", "marriott", "ihg", "choice", "wyndham"})
)
def test_ingest_accepts_every_rooms_aero_hotel_slug(ingestable: str, slug: str) -> None:
    _ingest(ingestable, {"J-CUN": stay_entry(rooms=[room(program=slug)])})
    doc = json.loads(trips.artifact_read(ingestable, "stays.json"))
    assert doc["stays"]["J-CUN"]["rooms"][0]["program"] == slug


def test_ingest_rejects_non_integer_cash_cents(ingestable: str) -> None:
    bad = {"J-CUN": stay_entry(rooms=[room(offers=[offer(cents=168.46)])])}
    with pytest.raises(UsageError, match="cash_per_night_cents"):
        _ingest(ingestable, bad)


def test_ingest_rejects_a_missing_provenance_block(ingestable: str) -> None:
    entry = stay_entry()
    del entry["provenance"]
    with pytest.raises(UsageError, match="provenance"):
        _ingest(ingestable, {"J-CUN": entry})


def test_ingest_rejects_an_out_of_enum_search_state(ingestable: str) -> None:
    entry = {**stay_entry(), "search_state": "no_space"}
    with pytest.raises(UsageError, match="search_state"):
        _ingest(ingestable, {"J-CUN": entry})


def test_ingest_rejects_a_non_object_payload(ingestable: str) -> None:
    with pytest.raises(UsageError, match="'stays' map"):
        stays.ingest(ingestable, json.dumps([1, 2, 3]), now=clock())


# --- finalize threading (deliverable 4) ----------------------------------------------------------


def _write_finalize_inputs(
    slug: str, *, ranked: list[dict], leads: list[dict] | None = None
) -> None:
    write_rank(slug, ranked)
    expand = {
        "journeys": [],
        "unpaired_outbounds": leads or [],
        "gated": [],
        "search_states": {},
        "leg_states": {},
        "provenance": {"fetched_at": FROZEN.isoformat(), "quota_stopped": False},
    }
    trips.artifact_write(slug, "expand.json", json.dumps(expand))


def test_finalize_attaches_the_walked_stay_and_defers_open_jaws(getaway_home: Path) -> None:
    slug = _trip(ROUND_TRIP_LODGING)
    lead = {"outbound": {"id": "OB", "dest": "LIR", "mileage": 70000}, "return_search_state": {}}
    _write_finalize_inputs(
        slug, ranked=[journey("J-CUN"), journey("J-OJ", ret_origin="MEX")], leads=[lead]
    )
    trips.artifact_write(
        slug,
        "stays.json",
        json.dumps({"generated_at": FROZEN.isoformat(), "stays": {"J-CUN": stay_entry()}}),
    )
    doc = factors.finalize(slug, now=clock())
    walked, open_jaw = doc["journeys"]
    assert walked["stays"]["rooms"][0]["program"] == "hyatt"
    assert "lodging_search" not in walked
    assert open_jaw["lodging_search"] == {"state": "deferred", "reason": "no_checkout"}
    assert doc["unpaired_leads"][0]["lodging_search"] == {
        "state": "deferred",
        "reason": "no_checkout",
    }


def test_finalize_marks_a_never_walked_journey_not_walked(getaway_home: Path) -> None:
    # A walkable journey absent from stays.json is a walk gap, surfaced honestly (never "no space").
    slug = _trip(ROUND_TRIP_LODGING)
    _write_finalize_inputs(slug, ranked=[journey("J-CUN")])
    trips.artifact_write(
        slug, "stays.json", json.dumps({"generated_at": FROZEN.isoformat(), "stays": {}})
    )
    (entry,) = factors.finalize(slug, now=clock())["journeys"]
    assert entry["lodging_search"] == {"state": "unavailable", "reason": "not_walked"}


def test_finalize_walks_a_cash_hybrid_with_observed_arrival(getaway_home: Path) -> None:
    # The flagship case — award to a gateway, cash hop onward, stay at the onward dest — walks on
    # the hop's real observed arrival, threaded through the board path.
    slug = _trip(ROUND_TRIP_LODGING)
    _write_finalize_inputs(slug, ranked=[hybrid_journey("H-OKA", gateway="NRT", onward="OKA")])
    trips.artifact_write(
        slug,
        "stays.json",
        json.dumps({"generated_at": FROZEN.isoformat(), "stays": {"H-OKA": stay_entry()}}),
    )
    (entry,) = factors.finalize(slug, now=clock())["journeys"]
    assert entry["stays"]["rooms"][0]["program"] == "hyatt"


def test_finalize_defers_a_cash_hybrid_without_observed_arrival(getaway_home: Path) -> None:
    # A cash hop with no arrival clock on the board: finalize threads a deferred lodging_search.
    slug = _trip(ROUND_TRIP_LODGING)
    j = hybrid_journey("H-OKA", gateway="NRT", onward="OKA")
    del j["fit_facts"]["legs"][1]["arrives_local"]
    _write_finalize_inputs(slug, ranked=[j])
    (entry,) = factors.finalize(slug, now=clock())["journeys"]
    assert entry["lodging_search"] == {"state": "deferred", "reason": "unknown_arrival"}


def test_finalize_without_lodging_threads_no_lodging_fields(getaway_home: Path) -> None:
    plan = {
        "trip_type": "round_trip",
        "origins": ["SFO"],
        "buckets": [{"name": "warm", "dests": ["CUN"]}],
    }
    slug = _trip(plan)
    _write_finalize_inputs(slug, ranked=[journey("J-CUN")])
    (entry,) = factors.finalize(slug, now=clock())["journeys"]
    assert "stays" not in entry
    assert "lodging_search" not in entry
