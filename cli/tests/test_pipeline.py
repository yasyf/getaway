import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import respx
from _api import api_row
from click.testing import CliRunner

from getaway import trips
from getaway.cli import cli
from getaway.paths import cache_db
from getaway.store import connect

SLUG = "2026-09-warm-beachy-week"
SEARCH_URL = "https://seats.aero/partnerapi/search"
FROZEN = dt.datetime(2026, 9, 1, 12, 0, 0, tzinfo=dt.timezone.utc)

PLAN = {
    "trip_type": "round_trip",
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT"]}],
}

OUTBOUND_ROWS = [
    api_row("OB", "SFO", "NRT", "2026-09-05", "united", {"J": {"mileage": "80000", "seats": 2}})
]
RETURN_ROWS = [
    api_row(
        "RET",
        "NRT",
        "SFO",
        "2026-09-12",
        "united",
        {"J": {"mileage": "75000", "seats": 2}},
        origin_region="Asia",
        dest_region="North America",
    )
]


def clock() -> Callable[[], dt.datetime]:
    return lambda: FROZEN


def _seg(origin: str, dest: str, dep: str, arr: str) -> dict:
    return {
        "origin": origin,
        "dest": dest,
        "departs_local": dep,
        "arrives_local": arr,
        "duration_minutes": 600,
        "cabin": "J",
        "carrier": "UA",
        "flight_number": "UA1",
        "aircraft": "77W",
    }


def _detail(cid: str, segments: list[dict], mileage: int) -> dict:
    return {
        "id": cid,
        "mileage": mileage,
        "total_taxes": 120,
        "taxes_currency": "USD",
        "remaining_seats": 2,
        "total_duration": 600,
        "segments": segments,
        "layovers": [],
        "booking_links": [{"label": "book", "link": "https://x", "primary": True}],
    }


@pytest.fixture
def runner(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    return CliRunner()


def invoke(runner: CliRunner, *args: str, stdin: str | None = None) -> str:
    result = runner.invoke(cli, list(args), input=stdin)
    assert result.exit_code == 0, f"{args}: {result.output} {result.exception}"
    return result.output


def search_responder(request: httpx.Request) -> httpx.Response:
    origins = dict(request.url.params)["origin_airport"]
    rows = OUTBOUND_ROWS if "SFO" in origins else RETURN_ROWS
    return httpx.Response(
        200, json={"data": rows, "hasMore": False}, headers={"X-RateLimit-Remaining": "900"}
    )


@respx.mock
def test_full_journey_pipeline_end_to_end(runner: CliRunner) -> None:
    respx.get(SEARCH_URL).mock(side_effect=search_responder)

    invoke(runner, "prefs", "init")
    invoke(runner, "trip", "new", SLUG)
    invoke(
        runner,
        "trip",
        "set",
        SLUG,
        stdin=json.dumps(
            {
                "cabin": "business",
                "party": 2,
                "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 8},
                "plan": PLAN,
            }
        ),
    )

    invoke(runner, "sweep", "run", SLUG, "outbound:asia")
    shortlist_ob = json.loads(invoke(runner, "shortlist", "run", SLUG, "--leg", "outbound"))
    assert [c["id"] for c in shortlist_ob["candidates"]] == ["OB"]
    invoke(runner, "sweep", "run", SLUG, "return")
    shortlist_ret = json.loads(invoke(runner, "shortlist", "run", SLUG, "--leg", "return"))
    assert [c["id"] for c in shortlist_ret["candidates"]] == ["RET"]

    # Seed the live-expanded /trips rows the Expand phase would fetch (cache-first, no HTTP).
    store = connect(cache_db(), now=clock())
    store.trip_detail_put(
        "OB", _detail("OB", [_seg("SFO", "NRT", "2026-09-05T10:00", "2026-09-06T14:00")], 80000)
    )
    store.trip_detail_put(
        "RET", _detail("RET", [_seg("NRT", "SFO", "2026-09-12T16:00", "2026-09-12T10:00")], 75000)
    )

    expand = json.loads(invoke(runner, "expand", "run", SLUG))
    assert expand == {"journeys": 1, "unpaired": 0, "gated": 0}
    journey = json.loads(trips.artifact_read(SLUG, "expand.json"))["journeys"][0]
    assert [leg["role"] for leg in journey["legs"]] == ["outbound", "return"]
    assert journey["cost"]["mileage"]["by_program"] == {"united": 155000}

    # The Assess agent's per-journey verdicts, keyed by journey id.
    trips.artifact_write(
        SLUG,
        "assess.json",
        json.dumps(
            {
                "journeys": {
                    journey["id"]: {
                        "verdicts": [
                            {
                                "factor": "seat_quality",
                                "leg": "outbound",
                                "verdict": "promote",
                                "evidence": "SQ suites",
                            }
                        ]
                    }
                },
                "notable_stretches": [],
            }
        ),
    )

    ranked = json.loads(invoke(runner, "rank", SLUG))
    assert [e["journey"]["id"] for e in ranked] == [journey["id"]]

    finalists = json.loads(invoke(runner, "trip", "finalize", SLUG))
    assert finalists["trip_type"] == "round_trip"
    assert [e["journey"]["id"] for e in finalists["journeys"]] == [journey["id"]]
    assert "hybrids" not in finalists  # journeys is the one class; no separate hybrids key
    assert finalists["unpaired_leads"] == []

    checkpoints = json.loads((trips.trip_dir(SLUG) / "checkpoints.json").read_text())
    assert set(checkpoints) >= {
        "sweep:outbound:asia",
        "shortlist:outbound",
        "sweep:return",
        "shortlist:return",
        "expand",
        "rank",
        "finalize",
    }


@respx.mock
def test_window_edit_flips_downstream_stale(runner: CliRunner) -> None:
    respx.get(SEARCH_URL).mock(side_effect=search_responder)
    invoke(runner, "prefs", "init")
    invoke(runner, "trip", "new", SLUG)
    invoke(
        runner,
        "trip",
        "set",
        SLUG,
        stdin=json.dumps(
            {
                "cabin": "business",
                "party": 2,
                "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 8},
                "plan": PLAN,
            }
        ),
    )
    invoke(runner, "sweep", "run", SLUG, "outbound:asia")
    invoke(runner, "shortlist", "run", SLUG, "--leg", "outbound")
    assert trips.status(SLUG, now=clock())["phase_map"]["shortlist:outbound"] == "fresh"
    invoke(
        runner,
        "trip",
        "set",
        SLUG,
        stdin=json.dumps(
            {"window": {"start": "2026-10-01", "end": "2026-10-30", "trip_length_days": 8}}
        ),
    )
    status = trips.status(SLUG, now=clock())
    assert status["phase_map"]["sweep:outbound:asia"] == "stale"
    assert status["phase_map"]["shortlist:outbound"] == "stale"
