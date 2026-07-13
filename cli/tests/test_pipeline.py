import json
from pathlib import Path

import httpx
import pytest
import respx
from _api import api_row
from click.testing import CliRunner

from getaway import trips
from getaway.cli import cli

SLUG = "2026-09-warm-beachy-week"
SEARCH_URL = "https://seats.aero/partnerapi/search"
AVAILABILITY_URL = "https://seats.aero/partnerapi/availability"

PLAN = {
    "origins": ["SFO"],
    "buckets": [{"name": "asia", "dests": ["NRT", "HND"]}],
    "program_sweeps": [{"source": "aeroplan", "dest_region": "Asia"}],
    "max_finalists": 3,
}

def jrow(rid: str, dest: str, date: str, source: str, mileage: str) -> dict:
    return api_row(rid, "SFO", dest, date, source, {"J": {"mileage": mileage, "seats": 2}})


SEARCH_ROWS = [
    jrow("R1", "NRT", "2026-09-05", "united", "80000"),
    jrow("R2", "HND", "2026-09-06", "united", "90000"),
]
AVAILABILITY_ROWS = [jrow("R3", "KIX", "2026-09-07", "aeroplan", "70000")]


@pytest.fixture
def runner(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("SEATS_AERO_API_KEY", "testkey")
    return CliRunner()


def invoke(runner: CliRunner, *args: str, stdin: str | None = None) -> str:
    result = runner.invoke(cli, list(args), input=stdin)
    assert result.exit_code == 0, f"{args}: {result.output} {result.exception}"
    return result.output


def json_out(output: str) -> dict:
    return json.loads(output)


@respx.mock
def test_full_pipeline_resume_and_stale_flip(runner: CliRunner) -> None:
    search = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": SEARCH_ROWS, "hasMore": False},
            headers={"X-RateLimit-Remaining": "900"},
        )
    )
    availability = respx.get(AVAILABILITY_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": AVAILABILITY_ROWS, "hasMore": False},
            headers={"X-RateLimit-Remaining": "880"},
        )
    )

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
                "party": 1,
                "window": {"start": "2026-09-01", "end": "2026-09-30", "trip_length_days": 12},
                "plan": PLAN,
            }
        ),
    )

    # Phase 1 — the two sweeps each spend exactly one seats.aero call.
    sweep_asia = json_out(invoke(runner, "sweep", "run", SLUG, "asia"))
    assert sweep_asia == {"label": "asia", "rows": 2, "new": 2, "quota_remaining": 900}
    sweep_prog = json_out(invoke(runner, "sweep", "run", SLUG, "aeroplan-asia"))
    assert sweep_prog == {"label": "aeroplan-asia", "rows": 1, "new": 1, "quota_remaining": 880}
    assert search.call_count == 1
    assert availability.call_count == 1

    # Phase 2 — offline SQL shortlist over both sweeps, no HTTP.
    shortlist_doc = json_out(invoke(runner, "shortlist", "run", SLUG))
    assert shortlist_doc["considered"] == 3
    assert [c["id"] for c in shortlist_doc["candidates"]] == ["R3", "R1", "R2"]

    # Seed the Expand and Assess phase artifacts the workflow would produce.
    trips.artifact_write(
        SLUG, "expand.json", json.dumps({"R3": {"product": "solid", "mileage": 70000}})
    )
    trips.artifact_write(
        SLUG,
        "assess.json",
        json.dumps(
            {
                "R3": {"seat_quality": {"verdict": "promote", "evidence": "SQ suites"}},
                "R1": {"seat_quality": {"verdict": "neutral", "evidence": "solid business"}},
            }
        ),
    )

    ranked = json_out(invoke(runner, "rank", SLUG))
    # seat_quality promote lifts R3; R1/R2 follow by mileage within their bands.
    assert [e["candidate"]["id"] for e in ranked] == ["R3", "R1", "R2"]
    assert ranked[0]["factors"] == {"seat_quality": {"verdict": "promote", "evidence": "SQ suites"}}
    assert ranked[0]["facts"]["afford"]["shortfall"] == 70000  # no balances → annotated, not gated

    finalists = json_out(invoke(runner, "trip", "finalize", SLUG))
    assert finalists["hybrids"] == []
    assert [d["candidate"]["id"] for d in finalists["directs"]] == ["R3", "R1", "R2"]
    assert finalists["directs"][0]["factors"] == {
        "seat_quality": {"verdict": "promote", "evidence": "SQ suites"}
    }

    checkpoints = json.loads((trips.trip_dir(SLUG) / "checkpoints.json").read_text())
    assert set(checkpoints) >= {
        "sweep:asia",
        "sweep:aeroplan-asia",
        "shortlist",
        "rank",
        "finalize",
    }

    # Zero-quota resume: re-running a fresh sweep skips wholesale, no new HTTP.
    again = json_out(invoke(runner, "sweep", "run", SLUG, "asia"))
    assert again == {"label": "asia", "skipped": True, "rows": 2}
    assert search.call_count == 1

    status_fresh = trips.status(SLUG)
    assert status_fresh["phase_map"]["sweep:asia"] == "fresh"
    assert status_fresh["phase_map"]["shortlist"] == "fresh"
    assert status_fresh["quota"]["remaining"] == 880

    # Editing the trip window invalidates every phase whose fingerprint consumes it.
    invoke(
        runner,
        "trip",
        "set",
        SLUG,
        stdin=json.dumps(
            {"window": {"start": "2026-10-01", "end": "2026-10-30", "trip_length_days": 12}}
        ),
    )
    status_stale = trips.status(SLUG)
    assert status_stale["phase_map"]["sweep:asia"] == "stale"
    assert status_stale["phase_map"]["shortlist"] == "stale"
    assert status_stale["phase_map"]["rank"] == "stale"
