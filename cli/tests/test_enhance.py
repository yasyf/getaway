import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from _api import expand_doc

from getaway import enhance, prefs, quality, trips
from getaway.paths import StateConflictError, UsageError

FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SLUG = "2026-09-warm"
RUNNER = str(Path(__file__).parent / "_runner.py")

PLAN = {
    "legs": [
        {"id": "outbound", "origins": ["SFO"], "buckets": [{"name": "asia", "dests": ["NRT"]}]},
        {"id": "return", "dests": "$origins"},
    ]
}
WINDOW = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


@pytest.fixture
def trip(getaway_home: Path) -> str:
    prefs.init()
    trips.new(SLUG, now=clock())
    trips.set_patch(SLUG, {"cabin": "business", "party": 2, "window": WINDOW, "plan": PLAN})
    return SLUG


def award_leg(
    role: str,
    source: str,
    *,
    lid: str | None = None,
    cabin: str = "J",
    airlines: str = "UA",
    seats: int = 2,
    mileage: int = 80000,
    origin: str = "SFO",
    dest: str = "NRT",
    carrier: str = "UA",
    carrier_name: str = "United Airlines",
    flight_number: str = "UA1",
    aircraft: str = "Boeing 777-300ER",
    aircraft_code: str = "77W",
) -> dict:
    return {
        "role": role,
        "id": lid or f"{role}-{source}",
        "cabin": cabin,
        "source": source,
        "mode": "award",
        "soft": False,
        "airlines": airlines,
        "fetched_at": None,
        "detail": {
            "mileage": mileage,
            "remaining_seats": seats,
            "booking_links": [
                {"label": "book", "link": f"https://{source}.example/book", "primary": True}
            ],
            "segments": [
                {
                    "origin": origin,
                    "dest": dest,
                    "departs_local": "2026-09-05T10:00",
                    "arrives_local": "2026-09-06T14:00",
                    "carrier": carrier,
                    "flight_number": flight_number,
                    "aircraft": aircraft,
                    "aircraft_code": aircraft_code,
                    "cabin": cabin,
                }
            ],
            "raw": {"carriers": {carrier: carrier_name}},
        },
    }


def leg_fact(
    *,
    state: str = "sufficient",
    count: int = 2,
    cache_age_hours: float | None = 1.0,
    origin: str = "SFO",
    dest: str = "NRT",
    departs: str = "2026-09-05T10:00",
    mode: str = "award",
) -> dict:
    return {
        "role": "outbound",
        "mode": mode,
        "origin": origin,
        "dest": dest,
        "departs_local": departs,
        "seat_sufficiency": {"state": state, "count": count},
        "cache_age_hours": cache_age_hours,
    }


def journey(jid: str, legs: list[dict], facts: list[dict]) -> dict:
    return {"id": jid, "legs": legs, "fit_facts": {"legs": facts}}


def lead(
    *,
    oid: str = "OB1",
    source: str = "united",
    dest: str = "NRT",
    cabin: str = "J",
    origin: str = "SFO",
    mileage: int = 70000,
    state: str = "searched_empty",
    verification: str | None = "unverified",
    searched_at: str | None = "2026-09-01T00:00:00+00:00",
    age: float | None = 30.0,
) -> dict:
    search_state = {"state": state}
    if verification is not None:
        search_state["verification"] = verification
    return {
        "outbound": {
            "id": oid,
            "cabin": cabin,
            "source": source,
            "dest": dest,
            "mileage": mileage,
            "detail": {
                "segments": [
                    {
                        "origin": origin,
                        "dest": dest,
                        "departs_local": "2026-09-05T10:00",
                        "arrives_local": "2026-09-06T14:00",
                    }
                ]
            },
        },
        "return_search_state": search_state,
        "searched_at": searched_at,
        "cache_age_hours": age,
    }


def write_expand(
    slug: str, *, journeys: list[dict] | None = None, unpaired: list[dict] | None = None
) -> None:
    trips.artifact_write(
        slug,
        "expand.json",
        json.dumps(expand_doc(journeys=journeys or [], unpaired_outbounds=unpaired or [])),
    )


def verify_row(
    target_id: str,
    outcome: str = "confirmed",
    *,
    checked_at: str = "2026-07-13T14:00:00+00:00",
    method: str = "cookie",
    observed: dict | None = None,
    evidence: str = "live-site check",
) -> dict:
    if outcome in ("confirmed", "degraded") and observed is None:
        observed = {"remaining_seats": 4}
    return {
        "target_id": target_id,
        "outcome": outcome,
        "checked_at": checked_at,
        "method": method,
        "observed": observed,
        "evidence": evidence,
    }


def enhancer_doc(rows: list[dict]) -> dict:
    return {"enhancer": "verify", "results": {r["target_id"]: r for r in rows}}


def read_verify(slug: str) -> dict:
    return json.loads(trips.artifact_read(slug, "enhance-verify.json"))


# ---- targets -------------------------------------------------------------------------------


def test_targets_select_unknown_seats_and_stale_cache_skip_fresh_sufficient(trip: str) -> None:
    js = [
        journey("UNK", [award_leg("outbound", "united", lid="unk")], [leg_fact(state="unknown")]),
        journey(
            "STALE",
            [award_leg("outbound", "alaska", lid="stale")],
            [leg_fact(state="sufficient", cache_age_hours=7.0)],  # past the 6h expand TTL
        ),
        journey(
            "FRESH",
            [award_leg("outbound", "united", lid="fresh")],
            [leg_fact(state="sufficient", cache_age_hours=1.0)],
        ),
        journey(
            "NOAGE",
            [award_leg("outbound", "united", lid="noage")],
            [leg_fact(state="sufficient", cache_age_hours=None)],
        ),
    ]
    write_expand(trip, journeys=js)
    by_id = {r["target_id"]: r for r in enhance.targets(trip, "verify")}
    assert set(by_id) == {"unk:J", "stale:J"}
    assert by_id["unk:J"]["reason"] == "seats_unknown"
    assert by_id["unk:J"]["gather_auth"] == "token"
    assert by_id["stale:J"]["reason"] == "stale_cache"
    assert by_id["stale:J"]["gather_auth"] == "cookie"


def test_target_row_carries_join_and_availability_fields(trip: str) -> None:
    js = [
        journey(
            "A",
            [award_leg("outbound", "alaska", lid="AV1", mileage=88000, seats=3)],
            [leg_fact(state="unknown", cache_age_hours=2.5, origin="SFO", dest="NRT")],
        )
    ]
    write_expand(trip, journeys=js)
    (row,) = enhance.targets(trip, "verify")
    assert row == {
        "target_id": "AV1:J",
        "kind": "award_leg",
        "reason": "seats_unknown",
        "availability_id": "AV1",
        "program": "alaska",
        "hosts": ["alaskaair.com"],
        "gather_auth": "cookie",
        "origin": "SFO",
        "dest": "NRT",
        "date": "2026-09-05",
        "cabin": "J",
        "airlines": "UA",
        "party": 2,
        "miles": 88000,
        "remaining_seats": 3,
        "cache_age_hours": 2.5,
        "booking_links": [
            {"label": "book", "link": "https://alaska.example/book", "primary": True}
        ],
        "site": "https://alaska.example/book",
        "journeys": ["A"],
    }


def test_cross_journey_dedupe_carries_all_referencing_journeys(trip: str) -> None:
    shared = award_leg("outbound", "united", lid="SHARED")
    js = [
        journey("J1", [shared], [leg_fact(state="unknown")]),
        journey("J2", [shared], [leg_fact(state="unknown")]),
    ]
    write_expand(trip, journeys=js)
    rows = enhance.targets(trip, "verify")
    assert [r["target_id"] for r in rows] == ["SHARED:J"]
    assert rows[0]["journeys"] == ["J1", "J2"]


def test_finalists_journeys_preferred_over_expand(trip: str) -> None:
    exp = journey(
        "EXP", [award_leg("outbound", "united", lid="EXPLEG")], [leg_fact(state="unknown")]
    )
    fin = journey(
        "FIN", [award_leg("outbound", "united", lid="FINLEG")], [leg_fact(state="unknown")]
    )
    write_expand(trip, journeys=[exp])
    finalists = {
        "trip_type": "round_trip",
        "journeys": [{"journey": fin}],
        "notable_stretches": [],
        "unpaired_leads": [],
        "search_states": {},
        "dropped": [],
    }
    trips.artifact_write(trip, "finalists.json", json.dumps(finalists))
    assert [r["target_id"] for r in enhance.targets(trip, "verify")] == ["FINLEG:J"]


def test_empty_lead_target_selected_and_non_qualifying_skipped(trip: str) -> None:
    leads = [
        lead(oid="Q", dest="NRT", source="united"),  # searched_empty + unverified → selected
        lead(oid="NR", dest="OKA", source="alaska", state="not_run", verification=None),
        lead(oid="V", dest="HND", source="united", verification="verified"),
    ]
    write_expand(trip, unpaired=leads)
    rows = enhance.targets(trip, "verify")
    assert [r["target_id"] for r in rows] == ["lead:NRT:J"]
    row = rows[0]
    assert row["kind"] == "empty_lead"
    assert row["reason"] == "searched_empty_unverified"
    assert row["origin"] == "NRT"  # the empty-return leg departs the outbound's destination
    assert row["dest"] == "SFO"
    assert row["date"] == "2026-09-14"  # trip window end
    assert row["cabin"] == "J"
    assert row["program"] == "united"
    assert row["gather_auth"] == "token"
    assert row["return_search_state"] == {"state": "searched_empty", "verification": "unverified"}


def test_leads_dedupe_by_dest_and_cabin(trip: str) -> None:
    write_expand(trip, unpaired=[lead(oid="A", dest="NRT"), lead(oid="B", dest="NRT")])
    assert [r["target_id"] for r in enhance.targets(trip, "verify")] == ["lead:NRT:J"]


def test_targets_combine_awards_and_leads_sorted_by_id(trip: str) -> None:
    js = [journey("A", [award_leg("outbound", "united", lid="zeb")], [leg_fact(state="unknown")])]
    write_expand(trip, journeys=js, unpaired=[lead(oid="OB", dest="NRT")])
    ids = [r["target_id"] for r in enhance.targets(trip, "verify")]
    assert ids == ["lead:NRT:J", "zeb:J"]  # sorted by target_id


def test_cash_leg_never_targeted(trip: str) -> None:
    cash = {"role": "onward", "id": "cash:X", "cabin": "J", "source": None, "mode": "cash"}
    write_expand(trip, journeys=[journey("H", [cash], [leg_fact(mode="cash")])])
    assert enhance.targets(trip, "verify") == []


def test_zero_targets_when_nothing_uncertain(trip: str) -> None:
    js = [
        journey(
            "A",
            [award_leg("outbound", "united")],
            [leg_fact(state="sufficient", cache_age_hours=1.0)],
        )
    ]
    write_expand(trip, journeys=js)
    assert enhance.targets(trip, "verify") == []


def test_unknown_program_on_award_leg_raises(trip: str) -> None:
    write_expand(
        trip,
        journeys=[
            journey("A", [award_leg("outbound", "madeup", lid="X")], [leg_fact(state="unknown")])
        ],
    )
    with pytest.raises(UsageError, match="unknown program 'madeup'"):
        enhance.targets(trip, "verify")


def test_unknown_program_on_lead_raises(trip: str) -> None:
    write_expand(trip, unpaired=[lead(source="madeup")])
    with pytest.raises(UsageError, match="unknown program 'madeup'"):
        enhance.targets(trip, "verify")


def test_missing_expand_raises_state_conflict(trip: str) -> None:
    with pytest.raises(StateConflictError, match="no expand.json"):
        enhance.targets(trip, "verify")


def test_unknown_enhancer_name_raises(trip: str) -> None:
    write_expand(trip)
    with pytest.raises(UsageError, match="unknown enhancer 'boost'"):
        enhance.targets(trip, "boost")


# ---- seat-advice targets ---------------------------------------------------------------------


def test_seat_advice_targets_dedupe_across_journeys(trip: str) -> None:
    shared = award_leg("outbound", "united", lid="SHARED")
    js = [
        journey("J1", [shared], [leg_fact(state="unknown")]),
        journey("J2", [shared], [leg_fact(state="unknown")]),
    ]
    write_expand(trip, journeys=js)
    rows = enhance.targets(trip, "seat-advice")
    assert [r["target_id"] for r in rows] == ["UA:77W:J"]
    assert rows[0]["journey_ids"] == ["J1", "J2"]
    assert rows[0]["flight_numbers"] == ["UA1"]


def test_seat_advice_target_row_carries_registry_and_target_id_shape(trip: str) -> None:
    js = [
        journey(
            "A",
            [
                award_leg(
                    "outbound",
                    "united",
                    lid="AV1",
                    carrier="ZZ",
                    carrier_name="Zephyr Air",
                    flight_number="ZZ42",
                    aircraft="Test Aircrafter 9000",
                    aircraft_code="T9K",
                    cabin="J",
                )
            ],
            [leg_fact(state="unknown")],
        )
    ]
    write_expand(trip, journeys=js)
    (row,) = enhance.targets(trip, "seat-advice")
    assert row == {
        "target_id": "ZZ:T9K:J",
        "carrier": "ZZ",
        "carrier_name": "Zephyr Air",
        "aircraft_code": "T9K",
        "aircraft_name": "Test Aircrafter 9000",
        "cabin": "J",
        "cabin_name": "business",
        "flight_numbers": ["ZZ42"],
        "journey_ids": ["A"],
        "registry": quality.classify("ZZ", "Test Aircrafter 9000", "business"),
    }
    # "ZZ" is not a real carrier in the seat-quality registry — the deterministic verify fallback.
    assert row["registry"] == {"verdict": "verify", "product": None, "note": None, "matched": None}


def test_seat_advice_targets_ignore_cash_legs(trip: str) -> None:
    cash = {"role": "onward", "id": "cash:X", "cabin": "J", "source": None, "mode": "cash"}
    write_expand(trip, journeys=[journey("H", [cash], [leg_fact(mode="cash")])])
    assert enhance.targets(trip, "seat-advice") == []


def test_seat_advice_targets_combine_flight_numbers_and_journeys_by_aircraft_key(trip: str) -> None:
    js = [
        journey(
            "A",
            [award_leg("outbound", "united", lid="A1", flight_number="UA100")],
            [leg_fact(state="unknown")],
        ),
        journey(
            "B",
            [award_leg("outbound", "united", lid="A2", flight_number="UA200")],
            [leg_fact(state="unknown")],
        ),
    ]
    write_expand(trip, journeys=js)
    (row,) = enhance.targets(trip, "seat-advice")
    assert row["flight_numbers"] == ["UA100", "UA200"]
    assert row["journey_ids"] == ["A", "B"]


def test_seat_advice_targets_include_notable_stretch_equipment(trip: str) -> None:
    finalist = journey(
        "FINALIST",
        [award_leg("outbound", "united", lid="FINALIST-LEG")],
        [leg_fact(state="unknown")],
    )
    notable = journey(
        "NOTABLE",
        [
            award_leg(
                "outbound",
                "united",
                lid="NOTABLE-LEG",
                aircraft="Airbus A350-900",
                aircraft_code="359",
            )
        ],
        [leg_fact(state="unknown")],
    )
    write_expand(trip, journeys=[])
    finalists = {
        "trip_type": "round_trip",
        "journeys": [{"journey": finalist}],
        "notable_stretches": [{"journey": notable, "why": "best seat despite the timing"}],
        "unpaired_leads": [],
        "search_states": {},
        "dropped": [],
    }
    trips.artifact_write(trip, "finalists.json", json.dumps(finalists))

    rows = enhance.targets(trip, "seat-advice")

    assert [row["target_id"] for row in rows] == ["UA:359:J", "UA:77W:J"]
    assert [row["journey_ids"] for row in rows] == [["NOTABLE"], ["FINALIST"]]


# ---- merge ---------------------------------------------------------------------------------


def test_merge_bootstraps_envelope_on_first_write(trip: str) -> None:
    returned = enhance.merge(trip, "verify", [verify_row("A:J")])
    doc = read_verify(trip)
    assert doc == returned
    assert doc["enhancer"] == "verify"
    assert set(doc["results"]) == {"A:J"}
    assert doc["results"]["A:J"]["outcome"] == "confirmed"


def test_merge_upserts_distinct_targets(trip: str) -> None:
    enhance.merge(trip, "verify", [verify_row("A:J")])
    enhance.merge(trip, "verify", [verify_row("B:J", outcome="gone", observed=None)])
    assert set(read_verify(trip)["results"]) == {"A:J", "B:J"}


def test_merge_later_checked_at_wins_earlier_ignored(trip: str) -> None:
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="confirmed", checked_at="2026-07-13T12:00:00+00:00")],
    )
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="gone", observed=None, checked_at="2026-07-13T15:00:00+00:00")],
    )
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="degraded", checked_at="2026-07-13T09:00:00+00:00")],
    )
    row = read_verify(trip)["results"]["A:J"]
    assert row["outcome"] == "gone"  # 15:00 beats the prior 12:00 and the later-submitted 09:00
    assert row["checked_at"] == "2026-07-13T15:00:00+00:00"


def test_merge_cross_offset_later_utc_wins_despite_lexical_order(trip: str) -> None:
    # 09:30+00:00 is chronologically later than 10:00+02:00 (=08:00 UTC), though lexically smaller.
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="gone", observed=None, checked_at="2026-07-13T09:30:00+00:00")],
    )
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="confirmed", checked_at="2026-07-13T10:00:00+02:00")],
    )
    row = read_verify(trip)["results"]["A:J"]
    assert row["outcome"] == "gone"  # 09:30Z (later UTC) survives the lexically-larger later write
    assert row["checked_at"] == "2026-07-13T09:30:00+00:00"


def test_merge_equal_timestamp_keeps_first_landed(trip: str) -> None:
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="confirmed", checked_at="2026-07-13T12:00:00+00:00")],
    )
    enhance.merge(
        trip,
        "verify",
        [verify_row("A:J", outcome="gone", observed=None, checked_at="2026-07-13T12:00:00+00:00")],
    )
    row = read_verify(trip)["results"]["A:J"]
    assert row["outcome"] == "confirmed"  # equal checked_at → tie keeps the first-landed row


def test_merge_rejects_non_array(trip: str) -> None:
    with pytest.raises(UsageError, match="expects a JSON array"):
        enhance.merge(trip, "verify", {"target_id": "A:J"})


def test_merge_rejects_bad_row(trip: str) -> None:
    with pytest.raises(UsageError, match="evidence"):
        enhance.merge(
            trip,
            "verify",
            [{"target_id": "A:J", "outcome": "confirmed", "checked_at": "x", "method": "cookie"}],
        )


def _run_merge(slug: str, rows_json: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, RUNNER, "enhance-merge", slug, "verify"],
        input=rows_json,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_concurrent_subprocess_merges_all_land(trip: str) -> None:
    env = os.environ.copy()
    payloads = [json.dumps([verify_row(f"T{i}:J")]) for i in range(8)]
    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        results = list(pool.map(lambda p: _run_merge(trip, p, env), payloads))
    for result in results:
        assert result.returncode == 0, result.stderr
    assert set(read_verify(trip)["results"]) == {f"T{i}:J" for i in range(8)}


# ---- seat-advice merge -----------------------------------------------------------------------


def seat_advice_row(
    target_id: str,
    outcome: str = "found",
    *,
    checked_at: str = "2026-07-13T14:00:00+00:00",
    method: str = "public",
    observed: dict | None = None,
    evidence: str = "seat guide consensus",
) -> dict:
    if outcome == "found" and observed is None:
        observed = {
            "picks": [{"seat": "12A", "why": "extra legroom"}],
            "avoids": [],
            "tips": [],
            "sources": ["https://example.com/seat-guru"],
        }
    return {
        "target_id": target_id,
        "outcome": outcome,
        "checked_at": checked_at,
        "method": method,
        "observed": observed,
        "evidence": evidence,
    }


def read_seat_advice(slug: str) -> dict:
    return json.loads(trips.artifact_read(slug, "enhance-seat-advice.json"))


def test_seat_advice_merge_accepts_valid_found_row(trip: str) -> None:
    enhance.merge(trip, "seat-advice", [seat_advice_row("UA:77W:J")])
    doc = read_seat_advice(trip)
    assert doc["enhancer"] == "seat-advice"
    assert doc["results"]["UA:77W:J"]["outcome"] == "found"


def test_seat_advice_merge_accepts_operated_by(trip: str) -> None:
    row = seat_advice_row("UA:77W:J")
    row["observed"]["operated_by"] = {"carrier": "MS", "name": "EgyptAir"}
    enhance.merge(trip, "seat-advice", [row])
    assert read_seat_advice(trip)["results"]["UA:77W:J"]["observed"] == row["observed"]


def test_seat_advice_merge_rejects_verify_only_outcome(trip: str) -> None:
    with pytest.raises(UsageError, match="outcome"):
        enhance.merge(
            trip, "seat-advice", [seat_advice_row("UA:77W:J", outcome="confirmed", observed=None)]
        )


def test_seat_advice_merge_rejects_found_with_all_advice_lists_empty(trip: str) -> None:
    empty = {"picks": [], "avoids": [], "tips": [], "sources": ["https://example.com/x"]}
    with pytest.raises(UsageError, match="at least one of picks, avoids, tips"):
        enhance.merge(trip, "seat-advice", [seat_advice_row("UA:77W:J", observed=empty)])


def test_seat_advice_merge_rejects_non_null_observed_on_inconclusive(trip: str) -> None:
    with pytest.raises(UsageError, match="observed must be null"):
        enhance.merge(
            trip,
            "seat-advice",
            [seat_advice_row("UA:77W:J", outcome="inconclusive", observed={"picks": []})],
        )


def test_seat_advice_merge_later_checked_at_wins_upsert(trip: str) -> None:
    enhance.merge(
        trip, "seat-advice", [seat_advice_row("UA:77W:J", checked_at="2026-07-13T12:00:00+00:00")]
    )
    later_observed = {
        "picks": [],
        "avoids": [{"seat": "30E", "why": "no recline, near the lavatory"}],
        "tips": [],
        "sources": ["https://example.com/y"],
    }
    enhance.merge(
        trip,
        "seat-advice",
        [
            seat_advice_row(
                "UA:77W:J", checked_at="2026-07-13T15:00:00+00:00", observed=later_observed
            )
        ],
    )
    row = read_seat_advice(trip)["results"]["UA:77W:J"]
    assert row["checked_at"] == "2026-07-13T15:00:00+00:00"
    assert row["observed"] == later_observed


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        pytest.param(
            lambda o: {**o, "sources": []}, "sources must be a non-empty list", id="empty-sources"
        ),
        pytest.param(
            lambda o: {**o, "sources": ["http://example.com/x"]},
            "must be an https URL",
            id="non-https-source",
        ),
        pytest.param(
            lambda o: {**o, "picks": [{"seat": "12A"}]}, "why", id="pick-missing-why"
        ),
        pytest.param(
            lambda o: {**o, "tips": [1]}, "must be a list of strings", id="non-string-tip"
        ),
        pytest.param(
            lambda o: {**o, "operated_by": {"carrier": "ms", "name": "EgyptAir"}},
            r"^seat-advice\.results\['UA:77W:J'\]\.observed\.operated_by\.carrier must match "
            r"\[A-Z0-9\]\{2\}$",
            id="lowercase-operating-carrier",
        ),
        pytest.param(
            lambda o: {**o, "operated_by": {"carrier": "EGY", "name": "EgyptAir"}},
            r"^seat-advice\.results\['UA:77W:J'\]\.observed\.operated_by\.carrier must match "
            r"\[A-Z0-9\]\{2\}$",
            id="three-character-operating-carrier",
        ),
        pytest.param(
            lambda o: {**o, "operated_by": {"carrier": "MS"}},
            r"^seat-advice\.results\['UA:77W:J'\]\.observed\.operated_by keys: missing="
            r"\['name'\] extra=\[\]$",
            id="operating-carrier-missing-name",
        ),
        pytest.param(
            lambda o: {**o, "operated_by": "MS"},
            r"^seat-advice\.results\['UA:77W:J'\]\.observed\.operated_by must be an object$",
            id="operating-carrier-non-object",
        ),
    ],
)
def test_validate_rejects_bad_seat_advice_observed(
    mutate: Callable[[dict], dict], match: str
) -> None:
    row = seat_advice_row("UA:77W:J")
    row["observed"] = mutate(row["observed"])
    doc = {"enhancer": "seat-advice", "results": {"UA:77W:J": row}}
    with pytest.raises(UsageError, match=match):
        enhance.validate_enhancer_doc(doc, "seat-advice")


# ---- validator -----------------------------------------------------------------------------


def test_validate_accepts_good_doc() -> None:
    enhance.validate_enhancer_doc(
        enhancer_doc([verify_row("A:J"), verify_row("B:J", outcome="gone", observed=None)]),
        "verify",
    )


def test_validate_accepts_public_method() -> None:
    enhance.validate_enhancer_doc(enhancer_doc([verify_row("A:J", method="public")]), "verify")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        pytest.param(lambda r: {**r, "outcome": "vanished"}, "outcome", id="bad-outcome"),
        pytest.param(
            lambda r: {**r, "outcome": ["gone"]}, "outcome must be a string", id="list-outcome"
        ),
        pytest.param(
            lambda r: {**r, "method": {"m": 1}}, "method must be a string", id="dict-method"
        ),
        pytest.param(
            lambda r: {**r, "outcome": "gone", "observed": {"x": 1}},
            "observed must be null",
            id="observed-on-gone",
        ),
        pytest.param(
            lambda r: {**r, "outcome": "confirmed", "observed": None},
            "observed must be an object",
            id="null-observed-on-confirmed",
        ),
        pytest.param(
            lambda r: {**r, "outcome": "degraded", "observed": None},
            "observed must be an object",
            id="null-observed-on-degraded",
        ),
        pytest.param(
            lambda r: {**r, "outcome": "inconclusive", "observed": {"x": 1}},
            "observed must be null",
            id="dict-observed-on-inconclusive",
        ),
        pytest.param(
            lambda r: {**r, "checked_at": "not-a-date"},
            "must be an ISO 8601 timestamp",
            id="unparsable-checked-at",
        ),
        pytest.param(
            lambda r: {**r, "checked_at": "2026-07-13T14:00:00"},
            "must be timezone-aware",
            id="naive-checked-at",
        ),
        pytest.param(
            lambda r: {k: v for k, v in r.items() if k != "evidence"}, "evidence", id="missing-key"
        ),
        pytest.param(lambda r: {**r, "method": "browser"}, "method", id="bad-method"),
    ],
)
def test_validate_rejects_bad_row(mutate: Callable[[dict], dict], match: str) -> None:
    row = mutate(verify_row("A:J"))
    doc = {"enhancer": "verify", "results": {"A:J": row}}
    with pytest.raises(UsageError, match=match):
        enhance.validate_enhancer_doc(doc, "verify")


def test_validate_rejects_target_id_key_mismatch() -> None:
    doc = {"enhancer": "verify", "results": {"WRONG:J": verify_row("A:J")}}
    with pytest.raises(UsageError, match="does not match key"):
        enhance.validate_enhancer_doc(doc, "verify")


def test_validate_rejects_unknown_enhancer_name() -> None:
    with pytest.raises(UsageError, match="enhancer"):
        enhance.validate_enhancer_doc({"enhancer": "boost", "results": {}}, "boost")


def test_validate_rejects_enhancer_name_mismatch() -> None:
    # A well-formed verify doc under a filename whose derived enhancer name is not "verify".
    with pytest.raises(UsageError, match="does not match filename enhancer"):
        enhance.validate_enhancer_doc({"enhancer": "verify", "results": {}}, "boost")


def test_validate_rejects_non_object_results() -> None:
    with pytest.raises(UsageError, match="results must be an object"):
        enhance.validate_enhancer_doc({"enhancer": "verify", "results": []}, "verify")


def test_artifact_write_accepts_good_enhance_doc(trip: str) -> None:
    good = json.dumps(enhancer_doc([verify_row("A:J")]))
    trips.artifact_write(trip, "enhance-verify.json", good)
    assert read_verify(trip) == json.loads(good)


def test_artifact_write_rejects_bad_enhance_doc(trip: str) -> None:
    bad = json.dumps(
        {
            "enhancer": "verify",
            "results": {
                "A:J": {
                    "target_id": "A:J",
                    "outcome": "nope",
                    "checked_at": "x",
                    "method": "cookie",
                    "observed": None,
                    "evidence": "e",
                }
            },
        }
    )
    with pytest.raises(UsageError, match="outcome"):
        trips.artifact_write(trip, "enhance-verify.json", bad)


def test_artifact_write_rejects_enhancer_filename_name_mismatch(trip: str) -> None:
    # The filename's derived enhancer ("boost") must match the doc's declared enhancer ("verify").
    doc = json.dumps({"enhancer": "verify", "results": {}})
    with pytest.raises(UsageError, match="does not match filename enhancer"):
        trips.artifact_write(trip, "enhance-boost.json", doc)


# ---- resume --------------------------------------------------------------------------------


def test_resume_lines_tally_results_and_latest_time(trip: str) -> None:
    enhance.merge(
        trip,
        "verify",
        [
            verify_row("A:J", outcome="confirmed", checked_at="2026-07-13T14:32:00+00:00"),
            verify_row(
                "B:J", outcome="gone", observed=None, checked_at="2026-07-13T13:00:00+00:00"
            ),
        ],
    )
    assert enhance.resume_lines(trip) == [
        "Enhancers: verify — 2 results (1 confirmed, 1 gone), latest 14:32"
    ]


def test_resume_lines_empty_without_artifact(trip: str) -> None:
    assert enhance.resume_lines(trip) == []
