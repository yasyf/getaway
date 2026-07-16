import datetime as dt
import json
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from getaway import prefs, trips
from getaway.constants import NOTABLE_PREFERENCE_STRETCH_LIMIT
from getaway.paths import StateConflictError, UsageError

SLUG = "2026-07-warm-beachy-week"


@pytest.fixture
def ready(getaway_home: Path) -> Path:
    prefs.init()
    return getaway_home


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_new_stamps_template(ready: Path, frozen_clock: Callable[[], dt.datetime]) -> None:
    doc = trips.new(SLUG, ask="somewhere warm", now=frozen_clock)
    assert doc == {
        "slug": SLUG,
        "created": "2026-07-13T12:00:00+00:00",
        "status": "planning",
        "ask": "somewhere warm",
        "window": {"start": None, "end": None, "trip_length_days": None},
        "cabin": None,
        "party": 1,
        "regions": {"include": [], "exclude": []},
        "vibe": [],
        "avoid_final_destinations": [],
        "plan": {},
        "judgment": {},
        "decisions": [],
    }
    assert json.loads((trips.trip_dir(SLUG) / "trip.json").read_text()) == doc


def test_new_sets_current_pointer_without_trailing_newline(ready: Path) -> None:
    trips.new(SLUG)
    pointer = trips.current_pointer()
    assert pointer.read_text() == SLUG
    assert not pointer.read_text().endswith("\n")


def test_new_refuses_existing_trip(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(StateConflictError):
        trips.new(SLUG)


def test_new_cli_exits_state_conflict_on_duplicate(ready: Path, runner: CliRunner) -> None:
    trips.new(SLUG)
    result = runner.invoke(trips.trip_group, ["new", SLUG])
    assert result.exit_code == 3


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda: trips.show(SLUG), id="show"),
        pytest.param(lambda: trips.set_patch(SLUG, {"cabin": "business"}), id="set-patch"),
        pytest.param(lambda: trips.done(SLUG), id="done"),
        pytest.param(lambda: trips.log(SLUG, "note"), id="log"),
    ],
)
def test_missing_trip_names_trips_dir(getaway_home: Path, operation: Callable[[], object]) -> None:
    with pytest.raises(StateConflictError) as exc:
        operation()
    message = str(exc.value)
    assert SLUG in message
    assert str(trips.trips_dir()) in message


@pytest.mark.parametrize("reserved", ["slug", "created"])
def test_set_patch_rejects_reserved_keys(ready: Path, reserved: str) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {reserved: "x"})


def test_set_patch_rejects_unknown_key(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"budget": 1000})


@pytest.mark.parametrize(
    ("patch", "key", "value"),
    [
        pytest.param({"cabin": "business"}, "cabin", "business", id="cabin"),
        pytest.param({"party": 3}, "party", 3, id="party"),
        pytest.param(
            {"window": {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}},
            "window",
            {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10},
            id="window",
        ),
        pytest.param({"vibe": ["beach", "food"]}, "vibe", ["beach", "food"], id="vibe"),
        pytest.param(
            {"regions": {"include": ["asia"], "exclude": ["north_america"]}},
            "regions",
            {"include": ["asia"], "exclude": ["north_america"]},
            id="regions",
        ),
    ],
)
def test_set_patch_merges_valid(ready: Path, patch: dict, key: str, value: object) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(SLUG, patch)
    assert doc[key] == value
    assert doc["slug"] == SLUG  # reserved keys survive the merge


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param({"cabin": "cattle"}, id="cabin-not-a-known-class"),
        pytest.param({"party": 0}, id="party-below-one"),
        pytest.param({"party": "two"}, id="party-not-int"),
        pytest.param({"window": {"start": "2026-09-01"}}, id="window-missing-keys"),
        pytest.param(
            {"window": {"start": 5, "end": None, "trip_length_days": None}},
            id="window-start-not-string",
        ),
        pytest.param({"regions": {"include": ["asia"]}}, id="regions-missing-exclude"),
        pytest.param({"vibe": "beach"}, id="vibe-not-list"),
    ],
)
def test_set_patch_rejects_invalid_shape(ready: Path, patch: dict) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, patch)


@pytest.mark.parametrize(
    "bad_slug",
    [
        pytest.param("../evil", id="parent-traversal"),
        pytest.param("a/b", id="embedded-slash"),
        pytest.param("Uppercase", id="uppercase"),
        pytest.param("a", id="too-short"),
        pytest.param("-leading", id="leading-hyphen"),
        pytest.param("has space", id="whitespace"),
    ],
)
def test_slug_traversal_rejected_everywhere(ready: Path, bad_slug: str) -> None:
    for call in (
        lambda: trips.new(bad_slug),
        lambda: trips.show(bad_slug),
        lambda: trips.set_patch(bad_slug, {"cabin": "business"}),
        lambda: trips.log(bad_slug, "note"),
        lambda: trips.current_set(bad_slug),
        lambda: trips.artifact_list(bad_slug),
        lambda: trips.artifact_write(bad_slug, "x.json", "{}"),
    ):
        with pytest.raises(UsageError):
            call()


def test_pointer_lifecycle_and_done_clears(ready: Path) -> None:
    trips.new(SLUG)
    assert trips.current_get() == SLUG
    other = "2026-11-ski-trip"
    trips.new(other)
    assert trips.current_get() == other
    trips.current_set(SLUG)
    assert trips.current_get() == SLUG
    done_doc = trips.done(SLUG)
    assert done_doc["status"] == "done"
    assert trips.current_get() is None


def test_done_leaves_unrelated_pointer(ready: Path) -> None:
    trips.new(SLUG)
    other = "2026-11-ski-trip"
    trips.new(other)  # current now points at other
    trips.done(SLUG)
    assert trips.current_get() == other


def test_done_clear_serialized_against_concurrent_current_set(
    ready: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trips.new(SLUG)  # current -> SLUG
    other = "2026-11-ski-trip"
    trips.new(other)  # current -> other
    trips.current_set(SLUG)  # current -> SLUG, so done(SLUG) will want to clear it
    pointer = trips.current_pointer()

    reached_read = threading.Event()
    setter_wrote = threading.Event()

    def concurrent_set() -> None:
        reached_read.wait()
        trips.current_set(other)  # blocks on the pointer lock while done() holds it
        setter_wrote.set()

    setter = threading.Thread(target=concurrent_set)
    setter.start()

    real_read_text = Path.read_text

    def read_text_hook(self: Path) -> str:
        value = real_read_text(self)
        if self == pointer and not reached_read.is_set():
            reached_read.set()
            # Fixed done() reads under the pointer lock, so the setter stays blocked
            # and this times out; the pre-fix read-then-unlink held no lock, letting
            # the setter write `other` into the gap before the unlink.
            setter_wrote.wait(timeout=1.0)
        return value

    monkeypatch.setattr(Path, "read_text", read_text_hook)
    trips.done(SLUG)
    setter.join(timeout=5)
    assert not setter.is_alive()
    assert trips.current_get() == other


def test_log_appends_decisions(ready: Path, frozen_clock: Callable[[], dt.datetime]) -> None:
    trips.new(SLUG)
    trips.log(SLUG, "picked BKK", now=frozen_clock)
    trips.log(SLUG, "ruled out ICN", now=frozen_clock)
    decisions = trips.show(SLUG)["decisions"]
    assert decisions == [
        {"ts": "2026-07-13T12:00:00+00:00", "text": "picked BKK"},
        {"ts": "2026-07-13T12:00:00+00:00", "text": "ruled out ICN"},
    ]


def test_list_reports_trips_with_docs(ready: Path) -> None:
    trips.new(SLUG)
    trips.new("2026-11-ski-trip")
    assert trips.list_() == [SLUG, "2026-11-ski-trip"]


def test_leg_dests_veto_binds_endpoints(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    trips.new(SLUG)
    trips.set_patch(SLUG, {"avoid_final_destinations": ["NRT"]})
    for bad in (["ICN"], ["NRT"]):
        with pytest.raises(UsageError, match="vetoed"):
            trips.set_patch(SLUG, {"plan": {"legs": [{"dests": bad, "mode": "award"}]}})
    ok = trips.set_patch(SLUG, {"plan": {"legs": [{"dests": ["BKK"], "mode": "award"}]}})
    assert ok["plan"]["legs"][0]["dests"] == ["BKK"]


def test_leg_buckets_not_vetoed_by_avoid_lists(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    trips.new(SLUG)
    # bucket dests are hub groupings, exempt from the destination veto (gateways always were)
    doc = trips.set_patch(
        SLUG,
        {"plan": {"legs": [{"mode": "award", "buckets": [{"name": "asia", "dests": ["ICN"]}]}]}},
    )
    assert doc["plan"]["legs"][0]["buckets"] == [{"name": "asia", "dests": ["ICN"]}]


def test_judgment_factor_ids_validated(ready: Path) -> None:
    trips.new(SLUG)
    ok = trips.set_patch(
        SLUG, {"judgment": {"factors": {"affordability": {"priority": "primary"}}}}
    )
    assert ok["judgment"]["factors"]["affordability"] == {"priority": "primary"}
    with pytest.raises(UsageError, match="factor id"):
        trips.set_patch(SLUG, {"judgment": {"factors": {"not_a_factor": {"priority": "primary"}}}})
    with pytest.raises(UsageError, match="priority"):
        trips.set_patch(SLUG, {"judgment": {"factors": {"affordability": {"priority": "vital"}}}})


def test_plan_allowlist_rejects_unknown_key(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": {"unexpected": 1}})
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": {"legs": [{"mode": "award", "dests": ["NRT"], "wat": 1}]}})


@pytest.mark.parametrize(
    "origins",
    [
        pytest.param("WST", id="bare-string"),
        pytest.param([1], id="non-string-member"),
    ],
)
def test_leg_origins_shape_validated(ready: Path, origins: object) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="origins"):
        trips.set_patch(
            SLUG, {"plan": {"legs": [{"origins": origins, "dests": ["NRT"], "mode": "award"}]}}
        )


def test_leg_origins_string_list_accepted(ready: Path) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(
        SLUG, {"plan": {"legs": [{"origins": ["WST"], "dests": ["NRT"], "mode": "award"}]}}
    )
    assert doc["plan"]["legs"][0]["origins"] == ["WST"]


def test_explicit_first_leg_origins_unchanged_by_preferences(ready: Path) -> None:
    prefs.set_patch({"home_airport": "SEA", "origin_airports": ["SFO", "OAK"]})
    trips.new(SLUG)
    trips.set_patch(
        SLUG, {"plan": {"legs": [{"origins": ["WST"], "dests": ["NRT"], "mode": "award"}]}}
    )

    assert trips.show(SLUG)["plan"]["legs"][0]["origins"] == ["WST"]


@pytest.mark.parametrize(
    ("pref_patch", "expected"),
    [
        pytest.param(
            {"home_airport": "SEA", "origin_airports": ["SFO", "OAK"]},
            ["SFO", "OAK"],
            id="origin-airports",
        ),
        pytest.param(
            {"home_airport": "SEA", "origin_airports": []},
            ["SEA"],
            id="home-airport-fallback",
        ),
    ],
)
def test_omitted_first_leg_origins_resolve_from_preferences(
    ready: Path, pref_patch: dict, expected: list[str]
) -> None:
    prefs.set_patch(pref_patch)
    trips.new(SLUG)
    trips.set_patch(SLUG, {"plan": {"legs": [{"dests": ["NRT"], "mode": "award"}]}})

    assert trips.show(SLUG)["plan"]["legs"][0]["origins"] == expected
    assert json.loads((trips.trip_dir(SLUG) / "trip.json").read_text())["plan"] == {
        "legs": [{"dests": ["NRT"], "mode": "award"}]  # storage stays sparse
    }


@pytest.mark.parametrize(
    ("key", "before", "after"),
    [
        pytest.param("home_airport", "SFO", "SEA", id="home-airport"),
        pytest.param("origin_airports", ["SFO"], ["OAK"], id="origin-airports"),
    ],
)
def test_sweep_fingerprint_tracks_origin_source_preferences(
    ready: Path, key: str, before: object, after: object
) -> None:
    prefs.set_patch({key: before})
    trips.new(SLUG)
    trips.set_patch(
        SLUG,
        {"plan": {"legs": [{"mode": "award", "buckets": [{"name": "asia", "dests": ["NRT"]}]}]}},
    )
    trip = trips.show(SLUG)
    before_fp = trips.capture_inputs_fp(trip, prefs.show(), "sweep:outbound:asia")

    prefs.set_patch({key: after})

    assert trips.capture_inputs_fp(trip, prefs.show(), "sweep:outbound:asia") != before_fp


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("week_end", id="underscore-breaks-artifact-regex"),
        pytest.param("Asia", id="uppercase"),
        pytest.param("-asia", id="leading-hyphen"),
        pytest.param("asia.jp", id="dot"),
        pytest.param("a" * 33, id="too-long"),
        pytest.param("", id="empty"),
        pytest.param("gateways", id="reserved-gateways"),
        pytest.param("onward", id="reserved-onward"),
    ],
)
def test_leg_bucket_name_validated(ready: Path, name: str) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(
            SLUG,
            {"plan": {"legs": [{"mode": "award", "buckets": [{"name": name, "dests": ["NRT"]}]}]}},
        )


def test_leg_bucket_valid_name_accepted(ready: Path) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(
        SLUG,
        {"plan": {"legs": [{"mode": "award", "buckets": [{"name": "asia-1", "dests": ["NRT"]}]}]}},
    )
    assert doc["plan"]["legs"][0]["buckets"] == [{"name": "asia-1", "dests": ["NRT"]}]


def test_leg_bucket_shape_validated(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(
            SLUG, {"plan": {"legs": [{"mode": "award", "buckets": [{"name": "asia"}]}]}}
        )  # missing dests
    with pytest.raises(UsageError):
        trips.set_patch(
            SLUG,
            {"plan": {"legs": [{"mode": "award", "buckets": [{"name": "asia", "dests": "NRT"}]}]}},
        )


def test_trip_set_cli_malformed_json_exits_usage(ready: Path, runner: CliRunner) -> None:
    # A stdin body that is not valid JSON maps to a usage error (exit 64), not a raw
    # JSONDecodeError traceback (exit 1).
    trips.new(SLUG)
    result = runner.invoke(trips.trip_group, ["set", SLUG], input="{not valid json")
    assert result.exit_code == 64


@pytest.mark.parametrize(
    ("name", "content"),
    [
        pytest.param("notes.json", '{"finalists": ["BKK", "SIN"]}', id="json"),
        pytest.param("sweep.jsonl", '{"route": "SFO-BKK"}\n{"route": "SFO-SIN"}\n', id="jsonl"),
    ],
)
def test_artifact_write_read_roundtrip(ready: Path, name: str, content: str) -> None:
    trips.new(SLUG)
    trips.artifact_write(SLUG, name, content)
    assert trips.artifact_read(SLUG, name) == content


def test_artifact_list_excludes_lock_sidecars(ready: Path) -> None:
    trips.new(SLUG)
    trips.artifact_write(SLUG, "sweep.jsonl", '{"a": 1}\n')
    trips.artifact_write(SLUG, "rank.json", "{}")
    assert trips.artifact_list(SLUG) == ["rank.json", "sweep.jsonl"]


@pytest.mark.parametrize(
    ("name", "content"),
    [
        pytest.param("sweep.jsonl", '{"a": 1}\n{bad}\n', id="jsonl-line-not-json"),
        pytest.param("rank.json", "{not json", id="json-not-parseable"),
    ],
)
def test_artifact_write_rejects_unparseable(ready: Path, name: str, content: str) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.artifact_write(SLUG, name, content)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("../escape.json", id="traversal"),
        pytest.param("Sweep.json", id="uppercase"),
        pytest.param("sweep.txt", id="wrong-extension"),
        pytest.param("sweep", id="no-extension"),
    ],
)
def test_artifact_name_rejected(ready: Path, name: str) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.artifact_write(SLUG, name, "{}")


def test_bridge_artifact_rejects_clockless_quote(ready: Path) -> None:
    # A hand-written quote missing its clocks used to crash `expand run` with a raw KeyError;
    # the write boundary must reject it loudly instead.
    trips.new(SLUG)
    quote = {
        "gateway": "NRT",
        "onward_dest": "OKA",
        "date": "2026-09-08",
        "cabin": "economy",
        "source": "fli",
        "price": 120.0,
        "currency": "USD",
        "duration_minutes": 180,
        "stops": 0,
        "airline": "JL",
        "flight_number": "JL1",
    }
    with pytest.raises(UsageError):
        trips.artifact_write(
            SLUG, "legs/outbound/bridge.json", json.dumps({"quotes": [quote], "failures": []})
        )


@pytest.mark.parametrize(
    ("currency", "accepted"),
    [
        pytest.param("USD", True, id="uppercase-iso-code"),
        pytest.param("usd", False, id="lowercase"),
        pytest.param("US", False, id="too-short"),
        pytest.param("USDD", False, id="too-long"),
        pytest.param("U5D", False, id="non-letter"),
    ],
)
def test_bridge_artifact_currency_code(ready: Path, currency: str, accepted: bool) -> None:
    trips.new(SLUG)
    quote = {
        "gateway": "NRT",
        "onward_dest": "OKA",
        "date": "2026-09-08",
        "cabin": "economy",
        "source": "fli",
        "price": 120.0,
        "currency": currency,
        "duration_minutes": 180,
        "stops": 0,
        "connections": [],
        "airline": "JL",
        "flight_number": "JL1",
        "departs_local": "2026-09-08T09:00",
        "arrives_local": "2026-09-08T12:00",
    }
    content = json.dumps({"quotes": [quote], "failures": []})

    if accepted:
        trips.artifact_write(SLUG, "legs/outbound/bridge.json", content)
    else:
        with pytest.raises(UsageError):
            trips.artifact_write(SLUG, "legs/outbound/bridge.json", content)


def _sweep_doc(
    superseded_rows: dict,
    *,
    search_states: dict | None = None,
    searched: list | None = None,
    completeness: str = "complete",
    rows: list | None = None,
) -> dict:
    return {
        "provenance": {
            "source": "all",
            "fetched_at": "2026-07-13T12:00:00+00:00",
            "searched": [{"start": "2026-09-01", "end": "2026-09-14"}]
            if searched is None
            else searched,
            "completeness": completeness,
            "expanded_origins": ["SFO"],
            "superseded_rows": superseded_rows,
        },
        "search_states": {"NRT": {"state": "complete"}} if search_states is None else search_states,
        "rows": [] if rows is None else rows,
    }


@pytest.mark.parametrize(
    "doc_kwargs",
    [
        pytest.param({"superseded_rows": {"count": 1, "ids": ["A"]}}, id="single"),
        pytest.param(
            {"superseded_rows": {"count": 60, "ids": [f"R{index:02d}" for index in range(50)]}},
            id="count-over-cap-ids-capped-at-fifty",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "completeness": "searched_empty"},
            id="searched-empty-market-still-supersedes",
        ),
        pytest.param(
            {
                "superseded_rows": {"count": 1, "ids": ["A"]},
                "completeness": "searched_empty",
                "search_states": {"NRT": {"state": "searched_empty"}},
            },
            id="searched-empty-endpoint-counts-as-fully-searched",
        ),
    ],
)
def test_sweep_artifact_accepts_coherent_superseded_rows(ready: Path, doc_kwargs: dict) -> None:
    trips.new(SLUG)
    trips.artifact_write(
        SLUG, "legs/outbound/sweep-asia.json", json.dumps(_sweep_doc(**doc_kwargs))
    )


@pytest.mark.parametrize(
    "doc_kwargs",
    [
        pytest.param({"superseded_rows": {"count": 2, "ids": ["A", "A"]}}, id="duplicate-ids"),
        pytest.param(
            {"superseded_rows": {"count": 3, "ids": ["A", "B"]}}, id="ids-shorter-than-count"
        ),
        pytest.param(
            {"superseded_rows": {"count": 60, "ids": [f"R{index:02d}" for index in range(49)]}},
            id="capped-count-with-forty-nine-ids",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "searched": []},
            id="searched-nothing",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "completeness": "partial"},
            id="partial-completeness",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "completeness": "failed"},
            id="failed-completeness",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "completeness": "not_run"},
            id="not-run-completeness",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "completeness": ["complete"]},
            id="unhashable-completeness-raises-usage-not-type-error",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "search_states": {"NRT": "partial"}},
            id="search-state-not-an-object",
        ),
        pytest.param(
            {
                "superseded_rows": {"count": 1, "ids": ["A"]},
                "search_states": {
                    "NRT": {"state": "complete"},
                    "BKK": {"state": "partial"},
                },
            },
            id="one-endpoint-not-generation-cutting",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "search_states": {"NRT": {}}},
            id="search-state-missing-state-key",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "searched": "2026-09-01"},
            id="searched-not-a-list",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "rows": ["not-an-object"]},
            id="row-not-an-object",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "rows": [{"ID": 7}]},
            id="row-id-not-a-string",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "rows": [{"noid": "x"}]},
            id="row-missing-id",
        ),
        pytest.param(
            {"superseded_rows": {"count": 1, "ids": ["A"]}, "rows": [{"ID": "A"}]},
            id="superseded-id-overlaps-own-row",
        ),
    ],
)
def test_sweep_artifact_rejects_incoherent_superseded_rows(ready: Path, doc_kwargs: dict) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.artifact_write(
            SLUG, "legs/outbound/sweep-asia.json", json.dumps(_sweep_doc(**doc_kwargs))
        )


@pytest.mark.parametrize(
    ("count", "accepted"),
    [
        pytest.param(NOTABLE_PREFERENCE_STRETCH_LIMIT, True, id="exactly-at-limit"),
        pytest.param(NOTABLE_PREFERENCE_STRETCH_LIMIT + 1, False, id="over-limit"),
    ],
)
def test_assess_notable_stretches_limit(ready: Path, count: int, accepted: bool) -> None:
    trips.new(SLUG)
    doc = {
        "journeys": {},
        "notable_stretches": [
            {"journey_id": f"J{i}", "why": f"notable preference stretch {i}"} for i in range(count)
        ],
    }

    if accepted:
        trips.artifact_write(SLUG, "assess.json", json.dumps(doc))
        assert json.loads(trips.artifact_read(SLUG, "assess.json")) == doc
    else:
        with pytest.raises(
            UsageError,
            match=rf"assess\.json\.notable_stretches must contain at most "
            rf"{NOTABLE_PREFERENCE_STRETCH_LIMIT} entries",
        ):
            trips.artifact_write(SLUG, "assess.json", json.dumps(doc))


def test_current_cli_reports_null_when_unset(ready: Path, runner: CliRunner) -> None:
    result = runner.invoke(trips.trip_group, ["current"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"current": None}


# --- A1: preferences/constraints plan model ---


@pytest.mark.parametrize(
    "v2_plan",
    [
        pytest.param({"trip_type": "one_way"}, id="trip_type"),
        pytest.param({"hybrid": {"gateways": ["NRT"], "onward_dests": ["OKA"]}}, id="hybrid"),
        pytest.param({"return": {"origins": ["KIX"], "dests": ["SFO"]}}, id="return"),
        pytest.param({"origins": ["SFO"]}, id="top-level-origins"),
        pytest.param({"buckets": [{"name": "asia", "dests": ["NRT"]}]}, id="top-level-buckets"),
    ],
)
def test_v2_plan_shape_rejected_loudly(ready: Path, v2_plan: dict) -> None:
    # v2 keys (trip_type/hybrid/return/top-level origins+buckets) are gone; no migration.
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": v2_plan})


def test_preference_with_ordinal_priority_accepted(ready: Path) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(
        SLUG,
        {"plan": {"preferences": {"cabin": {"value": "business", "priority": "primary"}}}},
    )
    assert doc["plan"]["preferences"]["cabin"] == {"value": "business", "priority": "primary"}


def test_preference_rejects_non_ordinal_priority(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="priority"):
        trips.set_patch(
            SLUG, {"plan": {"preferences": {"cabin": {"value": "business", "priority": 0.7}}}}
        )


def test_preference_rejects_unknown_key(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="preference key"):
        trips.set_patch(
            SLUG, {"plan": {"preferences": {"seat_pitch": {"value": 34, "priority": "note"}}}}
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("layover_style", "minimize", id="layover-style"),
        pytest.param("program_preference", ["aeroplan"], id="program-preference"),
    ],
)
def test_removed_preference_key_rejected(ready: Path, key: str, value: object) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match=rf"unknown preference key: '{key}'"):
        trips.set_patch(
            SLUG,
            {"plan": {"preferences": {key: {"value": value, "priority": "note"}}}},
        )


def test_preference_return_arrival_by_validates_iso_date(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="ISO date"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "preferences": {
                        "return_arrival_by": {
                            "value": {"latest_local_date": "next monday"},
                            "priority": "secondary",
                        }
                    }
                }
            },
        )


def test_constraint_requires_confirmed_flag(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="confirmed"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "constraints": {
                        "return_arrival_by": {"latest_local_date": "2026-09-14", "confirmed": False}
                    }
                }
            },
        )


def test_constraint_confirmed_accepted(ready: Path) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(
        SLUG,
        {"plan": {"constraints": {"mileage_limit": {"miles": 120000}}}},
    )
    assert doc["plan"]["constraints"]["mileage_limit"] == {"miles": 120000}


def test_same_key_in_both_branches_rejected(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="both preferences and constraints"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "preferences": {
                        "return_arrival_by": {
                            "value": {"latest_local_date": "2026-09-14"},
                            "priority": "primary",
                        }
                    },
                    "constraints": {
                        "return_arrival_by": {"latest_local_date": "2026-09-14", "confirmed": True}
                    },
                }
            },
        )


@pytest.mark.parametrize("branch", ["preferences", "constraints"])
def test_durable_pref_key_rejected_from_trip_doc(ready: Path, branch: str) -> None:
    trips.new(SLUG)
    payload = (
        {"value": ["ICN"], "priority": "note"} if branch == "preferences" else {"value": ["ICN"]}
    )
    with pytest.raises(UsageError, match="durable-preferences key"):
        trips.set_patch(SLUG, {"plan": {branch: {"avoid_destinations": payload}}})


def test_open_jaw_origins_veto_checked(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    trips.new(SLUG)
    with pytest.raises(UsageError, match="vetoed"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "legs": [
                        {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
                        {"origins": ["ICN"], "dests": "$origins", "mode": "award"},
                    ]
                }
            },
        )


def test_return_home_marker_exempt_from_veto(ready: Path) -> None:
    prefs.set_patch(
        {"avoid_destinations": ["SFO"]}
    )  # a vetoed home is still a valid $origins target
    trips.new(SLUG)
    doc = trips.set_patch(
        SLUG,
        {
            "plan": {
                "legs": [
                    {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
                    {"origins": ["KIX"], "dests": "$origins", "mode": "award"},
                ]
            }
        },
    )
    assert doc["plan"]["legs"][1]["dests"] == "$origins"


def test_origins_marker_rejected_on_first_leg(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="non-first"):
        trips.set_patch(SLUG, {"plan": {"legs": [{"dests": "$origins", "mode": "award"}]}})


def test_lodging_must_be_object(ready: Path) -> None:
    trips.new(SLUG)
    assert trips.set_patch(SLUG, {"plan": {"lodging": {}}})["plan"]["lodging"] == {}
    with pytest.raises(UsageError, match="lodging"):
        trips.set_patch(SLUG, {"plan": {"lodging": "a week somewhere warm"}})


@pytest.mark.parametrize(
    "plan",
    [
        pytest.param(5, id="int"),
        pytest.param(None, id="null"),
        pytest.param(True, id="bool"),
        pytest.param("a week somewhere warm", id="string"),
        pytest.param(["legs"], id="list"),
    ],
)
def test_set_patch_rejects_non_dict_plan(ready: Path, plan: object) -> None:
    # A non-dict plan value used to crash _reject_v2_plan's set(plan) with a raw TypeError; every
    # non-object plan must reject with a typed UsageError at the read boundary.
    trips.new(SLUG)
    with pytest.raises(UsageError, match="plan must be an object"):
        trips.set_patch(SLUG, {"plan": plan})


def test_resume_lists_expiring_instruments_across_variants(
    ready: Path, frozen_clock: Callable[[], dt.datetime]
) -> None:
    # resume() reads only the union's shared type + expires fields, so every instrument variant
    # renders (the credits→travel_instruments cutover consumer).
    trips.new(SLUG, now=frozen_clock)
    window = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}
    plan = {
        "legs": [
            {"origins": ["SFO"], "mode": "award", "buckets": [{"name": "a", "dests": ["NRT"]}]}
        ]
    }
    trips.set_patch(SLUG, {"cabin": "business", "party": 1, "window": window, "plan": plan})
    prefs.instrument_add(
        {
            "type": "monetary_credit",
            "issuer": "delta",
            "amount": 250.0,
            "currency": "USD",
            "expires": "2026-08-01",
        }
    )
    prefs.instrument_add({"type": "companion_fare", "issuer": "alaska", "expires": "2026-09-01"})
    out = trips.resume(SLUG, now=frozen_clock)
    assert "Instruments expiring within 90d:" in out
    assert "monetary_credit — expires 2026-08-01" in out
    assert "companion_fare — expires 2026-09-01" in out


# --- P2b: leg-intent compile fold ---

OUTBOUND = {"origins": ["SFO"], "mode": "award", "buckets": [{"name": "asia", "dests": ["NRT"]}]}
CANONICAL = {"legs": [OUTBOUND, {"dests": "$origins", "mode": "award"}]}
HYBRID = {
    "legs": [
        OUTBOUND,
        {"id": "hop", "dests": ["OKA"], "mode": "either"},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
MULTI_CITY = {
    "legs": [
        {
            "origins": ["SFO"],
            "dests": ["NRT"],
            "mode": "award",
            "stay_nights": {"min": 3, "max": 5},
        },
        {"id": "leg2", "dests": ["BKK"], "mode": "award", "stay_nights": {"min": 3, "max": 5}},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
CASH_HOP = {
    "legs": [
        OUTBOUND,
        {"id": "hop", "dests": ["OKA"], "mode": "cash"},
        {"id": "return", "dests": "$origins", "mode": "award"},
    ]
}
POSITIONING = {
    "legs": [
        {
            "origins": ["SFO"],
            "dests": ["LAX"],
            "mode": "cash",
            "optional": True,
            "role": "positioning",
        },
        {"id": "onward", "dests": ["NRT"], "mode": "award"},
    ]
}


def _graph(plan: dict, slug: str = SLUG, **top: object) -> dict:
    trips.new(slug)
    trips.set_patch(slug, {"cabin": "business", "plan": plan, **top})
    return trips.compile_graph(slug)


def _ids(graph: dict) -> list[str]:
    return [n["id"] for n in graph["nodes"]]


def _node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def test_conventional_two_intent_matches_round_trip_graph(ready: Path) -> None:
    graph = _graph(CANONICAL)
    assert _ids(graph) == [
        "sweep:outbound:asia",
        "shortlist:outbound",
        "sweep:return",
        "shortlist:return",
        "expand",
        "assess",
        "rank",
        "finalize",
    ]
    sweep_ob = _node(graph, "sweep:outbound:asia")
    assert sweep_ob["command"] == ["getaway", "sweep", "run", SLUG, "outbound:asia"]
    assert sweep_ob["inputs"] == []
    assert sweep_ob["outputs"] == ["legs/outbound/sweep-asia.json"]
    assert sweep_ob["endpoint_source"] is None
    sl_ob = _node(graph, "shortlist:outbound")
    assert sl_ob["command"] == ["getaway", "shortlist", "run", SLUG, "--leg", "outbound"]
    assert sl_ob["inputs"] == ["legs/outbound/sweep-asia.json"]
    assert sl_ob["outputs"] == ["legs/outbound/shortlist.json"]
    sweep_ret = _node(graph, "sweep:return")
    assert sweep_ret["command"] == ["getaway", "sweep", "run", SLUG, "return"]
    assert sweep_ret["inputs"] == ["legs/outbound/shortlist.json"]
    assert sweep_ret["outputs"] == ["legs/return/sweep.json"]
    assert sweep_ret["endpoint_source"] == {
        "from": "legs/outbound/shortlist.json",
        "field": "dest",
        "union": [],
        "override": None,
    }
    assert _node(graph, "shortlist:return")["command"] == [
        "getaway",
        "shortlist",
        "run",
        SLUG,
        "--leg",
        "return",
    ]
    assert _node(graph, "rank")["inputs"] == [
        "legs/outbound/shortlist.json",
        "legs/return/shortlist.json",
        "expand.json",
        "assess.json",
        "enhance-verify.json",
    ]
    assert _node(graph, "finalize")["inputs"] == ["rank.json", "enhance-verify.json"]
    assert _node(graph, "assess")["command"] is None
    assert graph["trip_type"] == "round_trip"
    assert graph["lodging"] is False
    assert graph["requires"] == []


def test_conventional_quota_budget_orders_legs_before_expand(ready: Path) -> None:
    budget = _graph(CANONICAL)["quota_budget"]
    ids = [n["id"] for n in budget["nodes"]]
    assert ids.index("sweep:outbound:asia") < ids.index("sweep:return") < ids.index("expand")
    assert budget["total"] == sum(n["quota_cost"] for n in budget["nodes"])


def test_explicit_conventional_ids_match_position_defaults(ready: Path) -> None:
    explicit = {
        "legs": [
            {"id": "outbound", **OUTBOUND},
            {"id": "return", "dests": "$origins", "mode": "award"},
        ]
    }
    assert _ids(_graph(CANONICAL, slug="trip-defaulted")) == _ids(
        _graph(explicit, slug="trip-explicit")
    )


def test_one_way_single_intent(ready: Path) -> None:
    graph = _graph({"legs": [OUTBOUND]})
    ids = _ids(graph)
    assert ids == [
        "sweep:outbound:asia",
        "shortlist:outbound",
        "expand",
        "assess",
        "rank",
        "finalize",
    ]
    assert not any(i.startswith("sweep:return") or i == "shortlist:return" for i in ids)
    assert graph["trip_type"] == "one_way"
    assert _node(graph, "rank")["inputs"] == [
        "legs/outbound/shortlist.json",
        "expand.json",
        "assess.json",
        "enhance-verify.json",
    ]


def test_three_intent_hybrid_award_and_cash_sides(ready: Path) -> None:
    graph = _graph(HYBRID)
    ids = _ids(graph)
    for expected in (
        "sweep:outbound:asia",
        "shortlist:outbound",
        "sweep:hop",
        "shortlist:hop",
        "pairs:hop",
        "bridge:hop",
        "sweep:return",
        "shortlist:return",
    ):
        assert expected in ids, expected
    pairs = _node(graph, "pairs:hop")
    assert pairs["kind"] == "onward"  # a registered kind; the id carries the leg
    assert pairs["command"] == ["getaway", "shortlist", "onward", SLUG, "--leg", "hop"]
    assert pairs["inputs"] == ["legs/outbound/shortlist.json", "legs/hop/sweep.json"]
    assert pairs["outputs"] == ["legs/hop/onward.json"]
    bridge = _node(graph, "bridge:hop")
    assert bridge["kind"] == "bridge"
    assert bridge["command"] == ["getaway", "bridge", SLUG, "--leg", "hop"]
    assert bridge["inputs"] == ["legs/hop/onward.json"]
    hop_sweep = _node(graph, "sweep:hop")
    assert hop_sweep["inputs"] == ["legs/outbound/shortlist.json"]
    assert hop_sweep["endpoint_source"]["from"] == "legs/outbound/shortlist.json"
    assert hop_sweep["endpoint_source"]["override"] == {"dests": ["OKA"]}
    # the pairs node carries the leg's endpoint_source verbatim — no override-blind pairs path.
    assert pairs["endpoint_source"] == hop_sweep["endpoint_source"]
    # the either hop carries its cash-reachable dests forward: sweep:return unions ["OKA"] like
    # HEAD's onward_dests, chained off the hop's own shortlist (its award lane).
    assert _node(graph, "sweep:return")["endpoint_source"] == {
        "from": "legs/hop/shortlist.json",
        "field": "dest",
        "union": ["OKA"],
        "override": None,
    }
    # expand reads every award shortlist plus the optional cash artifacts (absent hashes as absent)
    assert _node(graph, "expand")["inputs"] == [
        "legs/outbound/shortlist.json",
        "legs/hop/shortlist.json",
        "legs/return/shortlist.json",
        "legs/hop/onward.json",
        "legs/hop/bridge.json",
    ]


def test_multi_city_compiles_chained_sweep_shortlist_per_leg(ready: Path) -> None:
    graph = _graph(MULTI_CITY)
    assert _ids(graph)[:6] == [
        "sweep:outbound",
        "shortlist:outbound",
        "sweep:leg2",
        "shortlist:leg2",
        "sweep:return",
        "shortlist:return",
    ]
    assert _node(graph, "sweep:leg2")["inputs"] == ["legs/outbound/shortlist.json"]
    assert _node(graph, "sweep:leg2")["endpoint_source"]["from"] == "legs/outbound/shortlist.json"
    assert _node(graph, "sweep:return")["inputs"] == ["legs/leg2/shortlist.json"]
    assert _node(graph, "rank")["inputs"][:3] == [
        "legs/outbound/shortlist.json",
        "legs/leg2/shortlist.json",
        "legs/return/shortlist.json",
    ]
    assert graph["requires"] == []  # stay_nights marks stops but requests no lodging


def test_cash_hop_emits_no_sweep_and_return_chains_from_declared_dests(ready: Path) -> None:
    # award SFO→NRT, pure-cash hop NRT→OKA, award return $origins.
    graph = _graph(CASH_HOP)
    ids = _ids(graph)
    assert "sweep:hop" not in ids  # a pure-cash leg spends no seats.aero quota
    assert [i for i in ids if i.startswith("sweep:")] == ["sweep:outbound:asia", "sweep:return"]
    # the return leg anchors on the cash hop's declared dests, from-absent (no hop shortlist)
    sweep_ret = _node(graph, "sweep:return")
    assert sweep_ret["inputs"] == []
    assert sweep_ret["endpoint_source"] == {
        "field": "dest",
        "union": ["OKA"],
        "override": None,
    }
    # the cash hop prices NRT→OKA off the outbound shortlist; its pairs carry the chain source
    pairs = _node(graph, "pairs:hop")
    assert pairs["inputs"] == ["legs/outbound/shortlist.json"]
    assert pairs["endpoint_source"] == {
        "from": "legs/outbound/shortlist.json",
        "field": "dest",
        "union": [],
        "override": {"dests": ["OKA"]},
    }


def test_positioning_leg_compiles_no_award_sweep_and_onward_chains_from_cash_dests(
    ready: Path,
) -> None:
    # leading cash SFO→LAX, award onward LAX→NRT (origins omitted).
    graph = _graph(POSITIONING)
    assert _ids(graph) == [
        "pairs:outbound",
        "bridge:outbound",
        "sweep:onward",
        "shortlist:onward",
        "expand",
        "assess",
        "rank",
        "finalize",
    ]
    # only the award onward lane costs quota — the cash positioning leg emits no sweep
    budget = graph["quota_budget"]
    assert [n["id"] for n in budget["nodes"]] == ["sweep:onward", "expand"]
    assert budget["total"] == sum(n["quota_cost"] for n in budget["nodes"])
    # the award onward departs the positioning leg's declared dest, from-absent (cash prior)
    sweep_on = _node(graph, "sweep:onward")
    assert sweep_on["inputs"] == []
    assert sweep_on["endpoint_source"] == {
        "field": "dest",
        "union": ["LAX"],
        "override": {"dests": ["NRT"]},
    }
    assert _node(graph, "pairs:outbound")["endpoint_source"] is None  # first leg, no chain


def test_lodging_adds_stays_node_and_requires_session(ready: Path) -> None:
    graph = _graph({**CANONICAL, "lodging": {}})
    assert graph["requires"] == ["rooms_session"]
    stays = _node(graph, "stays")
    assert stays["requires"] == ["rooms_session"]
    assert stays["outputs"] == ["stays.json"]
    assert _node(graph, "finalize")["inputs"] == ["rank.json", "enhance-verify.json", "stays.json"]


def test_one_way_lodging_without_checkout_has_no_stays(ready: Path) -> None:
    # no flight home to $origins and no explicit checkout ⇒ no stay to derive
    graph = _graph({"legs": [OUTBOUND], "lodging": {}})
    assert graph["requires"] == []
    assert "stays" not in _ids(graph)


def test_discover_dests_validated_but_inert(ready: Path) -> None:
    plan = {
        "legs": [
            {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
            {"id": "explore", "dests": {"discover": {"region": "asia"}}, "mode": "award"},
        ]
    }
    doc = trips.set_patch(trips.new(SLUG)["slug"], {"plan": plan})
    assert doc["plan"]["legs"][1]["dests"] == {"discover": {"region": "asia"}}
    graph = trips.compile_graph(SLUG)
    assert not any("explore" in i for i in _ids(graph))  # discover emits no retrieval nodes in P2b


def test_compile_requires_legs(ready: Path) -> None:
    trips.new(SLUG)
    trips.set_patch(
        SLUG, {"plan": {"preferences": {"cabin": {"value": "business", "priority": "primary"}}}}
    )
    with pytest.raises(UsageError, match="legs"):
        trips.compile_graph(SLUG)


def test_checkpoints_key_by_node_id(ready: Path, frozen_clock: Callable[[], dt.datetime]) -> None:
    _graph({"legs": [OUTBOUND]})
    trips.phase_done(SLUG, "shortlist:outbound", now=frozen_clock)
    assert trips.phase_fresh(SLUG, "shortlist:outbound", now=frozen_clock)
    with pytest.raises(UsageError, match="unknown node id"):
        trips.phase_done(SLUG, "sweep:return", now=frozen_clock)  # no return leg on a one-way plan


def test_empty_legs_rejected(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="non-empty"):
        trips.set_patch(SLUG, {"plan": {"legs": []}})


def test_duplicate_leg_ids_rejected(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="unique"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "legs": [
                        {"id": "x", "origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
                        {"id": "x", "dests": "$origins", "mode": "award"},
                    ]
                }
            },
        )


def test_middle_leg_requires_explicit_id(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="requires an explicit id"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "legs": [
                        {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
                        {"dests": ["BKK"], "mode": "award"},
                        {"dests": "$origins", "mode": "award"},
                    ]
                }
            },
        )


@pytest.mark.parametrize(
    "bad_leg",
    [
        pytest.param({"mode": "teleport", "dests": ["NRT"]}, id="bad-mode"),
        pytest.param(
            {"mode": "award", "dests": ["NRT"], "stay_nights": {"min": 0, "max": 2}},
            id="stay-nonpositive",
        ),
        pytest.param(
            {"mode": "award", "dests": ["NRT"], "stay_nights": {"min": 5, "max": 2}},
            id="stay-min-gt-max",
        ),
        pytest.param(
            {"mode": "award", "dests": ["NRT"], "window": {"start": "nope", "end": "2026-09-14"}},
            id="window-bad-date",
        ),
        pytest.param({"mode": "award", "dests": ["NRT"], "cabin": "cattle"}, id="bad-cabin"),
        pytest.param(
            {"mode": "award", "dests": ["NRT"], "optional": "yes"}, id="optional-not-bool"
        ),
        pytest.param({"mode": "award"}, id="no-dests-no-buckets"),
        pytest.param({"id": "UP", "dests": ["NRT"], "mode": "award"}, id="bad-leg-id"),
    ],
)
def test_leg_strict_validator_rejects(ready: Path, bad_leg: dict) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": {"legs": [bad_leg]}})


def test_optional_and_role_and_window_validated_but_accepted(ready: Path) -> None:
    plan = {
        "legs": [
            {
                "origins": ["LAX"],
                "dests": ["SFO"],
                "mode": "cash",
                "optional": True,
                "role": "positioning",
                "window": {"start": "2026-09-01", "end": "2026-09-02"},
            },
            {"id": "onward", "dests": ["NRT"], "mode": "award"},
        ]
    }
    doc = trips.set_patch(trips.new(SLUG)["slug"], {"plan": plan})
    first = doc["plan"]["legs"][0]
    assert first["optional"] is True
    assert first["role"] == "positioning"


def test_origins_marker_rejected_as_list_member(ready: Path) -> None:
    # "$origins" is a whole-value marker; one character off (a list member) leaks it as an airport
    trips.new(SLUG)
    with pytest.raises(UsageError, match="whole-value marker"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "legs": [
                        {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
                        {"id": "return", "dests": ["$origins"], "mode": "award"},
                    ]
                }
            },
        )


@pytest.mark.parametrize(
    "op",
    [
        pytest.param(lambda: trips.show(SLUG), id="show"),
        pytest.param(lambda: trips.compile_graph(SLUG), id="compile"),
        # A patch that doesn't re-declare the plan merges the stored v2 plan back in and must reject
        # with the same loud remedy, not the generic extra-keys validator error.
        pytest.param(lambda: trips.set_patch(SLUG, {"cabin": "business"}), id="set-patch"),
    ],
)
def test_stored_v2_plan_rejected_loudly(ready: Path, op: Callable[[], object]) -> None:
    # A pre-cutover trip.json bypasses set_patch validation; show()/compile_graph/set_patch must
    # reject it loudly, naming the v2 keys and the re-declare remedy (no migration).
    trips.new(SLUG)
    path = trips._trip_json(SLUG)
    doc = json.loads(path.read_text())
    doc["plan"] = {
        "trip_type": "round_trip",
        "origins": ["SFO"],
        "buckets": [{"name": "asia", "dests": ["NRT"]}],
        "return": {"origins": ["NRT"], "dests": ["SFO"]},
    }
    path.write_text(json.dumps(doc))
    with pytest.raises(UsageError, match="v2 keys") as exc:
        op()
    message = str(exc.value)
    for key in ("buckets", "origins", "return", "trip_type"):
        assert key in message  # every offending key is named
    assert "trip set" in message  # and the remedy


def test_program_sweep_missing_region_rejected_not_attribute_error(ready: Path) -> None:
    # A regionless program_sweeps entry used to reach _leg_sweep_labels as a raw AttributeError;
    # it now fails loud at validation with a UsageError.
    trips.new(SLUG)
    with pytest.raises(UsageError, match="dest_region or origin_region"):
        trips.set_patch(
            SLUG,
            {"plan": {"legs": [{"origins": ["SFO"], "program_sweeps": [{"source": "aeroplan"}]}]}},
        )


def test_program_sweep_valid_entry_compiles_region_sweep(ready: Path) -> None:
    # A region is a seats.aero API operand — one of the six capitalized continents. The label
    # slugifies it (lower, spaces->hyphens); the stored value stays verbatim for the runtime call.
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "program_sweeps": [{"source": "aeroplan", "dest_region": "North America"}],
            },
            {"id": "return", "dests": "$origins", "mode": "award"},
        ]
    }
    graph = _graph(plan)
    assert "sweep:outbound:aeroplan-north-america" in _ids(graph)
    assert _node(graph, "sweep:outbound:aeroplan-north-america")["outputs"] == [
        "legs/outbound/sweep-aeroplan-north-america.json"
    ]
    stored = trips.show(SLUG)["plan"]["legs"][0]["program_sweeps"][0]
    assert stored["dest_region"] == "North America"  # capitalized name preserved for the API


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("a/b", id="slash"),
        pytest.param("Aeroplan", id="caps"),
        pytest.param("", id="empty"),
    ],
)
def test_program_sweep_source_rejects_non_leaf_grammar(ready: Path, source: str) -> None:
    # source folds into the node id, artifact leaf, and command token: it must match the leaf
    # grammar (lowercase alnum + hyphen), so nothing escapes into a path segment or the leg:label.
    trips.new(SLUG)
    sweep = {"source": source, "dest_region": "Asia"}
    with pytest.raises(UsageError, match="must match") as exc:
        trips.set_patch(SLUG, {"plan": {"legs": [{"origins": ["SFO"], "program_sweeps": [sweep]}]}})
    assert repr(source) in str(exc.value)


@pytest.mark.parametrize(
    "sweep,offender",
    [
        pytest.param({"source": "aeroplan", "dest_region": "asia"}, "asia", id="dest-slug"),
        pytest.param(
            {"source": "aeroplan", "dest_region": "Asia:East"}, "Asia:East", id="dest-colon"
        ),
        pytest.param({"source": "aeroplan", "origin_region": "a/b"}, "a/b", id="origin-slash"),
        pytest.param({"source": "aeroplan", "dest_region": ""}, "", id="dest-empty"),
    ],
)
def test_program_sweep_region_rejects_non_continent(
    ready: Path, sweep: dict, offender: str
) -> None:
    # A region is a seats.aero API operand from the closed continent vocabulary, not a free label:
    # a value outside it — even the slug 'asia' — rejects loudly, naming value and vocabulary.
    trips.new(SLUG)
    with pytest.raises(UsageError, match="must be one of") as exc:
        trips.set_patch(SLUG, {"plan": {"legs": [{"origins": ["SFO"], "program_sweeps": [sweep]}]}})
    message = str(exc.value)
    assert repr(offender) in message
    assert "North America" in message  # the vocabulary is named


@pytest.mark.parametrize(
    "leg,label,named",
    [
        pytest.param(
            {
                "origins": ["SFO"],
                "mode": "award",
                "buckets": [{"name": "aeroplan-asia", "dests": ["NRT"]}],
                "program_sweeps": [{"source": "aeroplan", "dest_region": "Asia"}],
            },
            "aeroplan-asia",
            ["bucket", "program_sweep"],
            id="bucket-vs-dest-program",
        ),
        pytest.param(
            {
                "origins": ["SFO"],
                "mode": "award",
                "program_sweeps": [
                    {"source": "aeroplan", "dest_region": "Asia"},
                    {"source": "aeroplan", "dest_region": "Asia"},
                ],
            },
            "aeroplan-asia",
            ["program_sweep"],
            id="two-identical-dest-program",
        ),
        pytest.param(
            {
                "origins": ["SFO"],
                "mode": "award",
                "program_sweeps": [
                    {"source": "aeroplan", "origin_region": "Asia"},
                    {"source": "aeroplan", "origin_region": "Asia"},
                ],
            },
            "aeroplan-from-asia",
            ["program_sweep"],
            id="two-identical-origin-program",
        ),
    ],
)
def test_duplicate_sweep_labels_reject_at_compile(
    ready: Path, leg: dict, label: str, named: list[str]
) -> None:
    # Two groupings folding to one label alias one node id and one artifact; validation accepts
    # each grouping, the collision surfaces at compile naming the colliding label and both sources.
    plan = {"legs": [leg, {"id": "return", "dests": "$origins", "mode": "award"}]}
    trips.new(SLUG)
    trips.set_patch(SLUG, {"plan": plan})
    with pytest.raises(UsageError, match="derives sweep label") as exc:
        trips.compile_graph(SLUG)
    message = str(exc.value)
    assert label in message
    for token in named:
        assert token in message


def test_program_sweep_dest_and_origin_region_coexist_via_direction_suffix(ready: Path) -> None:
    # A source's dest_region and origin_region sweep over one continent must not alias: the origin
    # side takes a "from-" infix so both compile to distinct sweep nodes and artifacts.
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "mode": "award",
                "program_sweeps": [
                    {"source": "aeroplan", "dest_region": "Asia"},
                    {"source": "aeroplan", "origin_region": "Asia"},
                ],
            },
            {"id": "return", "dests": "$origins", "mode": "award"},
        ]
    }
    graph = _graph(plan)
    ids = _ids(graph)
    assert "sweep:outbound:aeroplan-asia" in ids
    assert "sweep:outbound:aeroplan-from-asia" in ids
    assert _node(graph, "sweep:outbound:aeroplan-from-asia")["outputs"] == [
        "legs/outbound/sweep-aeroplan-from-asia.json"
    ]


@pytest.mark.parametrize(
    "cash_leg,message",
    [
        pytest.param(
            {"id": "hop", "mode": "cash", "buckets": [{"name": "asia", "dests": ["OKA"]}]},
            "award-lane groupings",
            id="buckets-only",
        ),
        pytest.param(
            {
                "id": "hop",
                "mode": "cash",
                "program_sweeps": [{"source": "aeroplan", "dest_region": "asia"}],
            },
            "award-lane groupings",
            id="program-sweeps-only",
        ),
        pytest.param(
            {"id": "hop", "mode": "cash", "dests": []}, "non-empty dests", id="empty-dests"
        ),
        pytest.param({"id": "hop", "mode": "cash"}, "non-empty dests", id="no-dests"),
    ],
)
def test_cash_leg_requires_concrete_anchor(ready: Path, cash_leg: dict, message: str) -> None:
    # A pure-cash leg has no award lane: no buckets/program_sweeps, no empty/absent dests — it must
    # anchor its successor on a concrete IATA list or $origins.
    plan = {"legs": [OUTBOUND, cash_leg, {"id": "return", "dests": "$origins", "mode": "award"}]}
    trips.new(SLUG)
    with pytest.raises(UsageError, match=message):
        trips.set_patch(SLUG, {"plan": plan})


def test_cash_leg_rejects_discover_dests(ready: Path) -> None:
    # A pure-cash leg has no award scout lane; discover dests are not a concrete anchor.
    plan = {"legs": [OUTBOUND, {"id": "hop", "mode": "cash", "dests": {"discover": {}}}]}
    trips.new(SLUG)
    with pytest.raises(UsageError, match="non-empty dests"):
        trips.set_patch(SLUG, {"plan": plan})


def test_cash_leg_dests_and_origins_marker_accepted(ready: Path) -> None:
    # The two legal cash-leg dests forms compile: a concrete IATA list and the $origins marker.
    concrete = _graph(CASH_HOP)  # NRT→OKA concrete list
    assert _node(concrete, "pairs:hop")["endpoint_source"]["override"] == {"dests": ["OKA"]}
    plan = {
        "legs": [
            {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
            {"id": "home", "dests": "$origins", "mode": "cash"},
        ]
    }
    marker = _graph(plan, slug="cash-home")
    assert _node(marker, "pairs:home")["endpoint_source"]["override"] is None


def test_cash_home_leg_anchors_next_leg_at_materialized_origins(ready: Path) -> None:
    # award SFO→NRT, cash home $origins, award second LHR: the second leg's sweep unions the
    # cash-home leg's resolved dests — the first leg's materialized origins ([SFO]), never [].
    plan = {
        "legs": [
            {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
            {"id": "home", "dests": "$origins", "mode": "cash"},
            {"id": "second", "dests": ["LHR"], "mode": "award"},
        ]
    }
    graph = _graph(plan)
    assert "sweep:home" not in _ids(graph)  # a pure-cash leg spends no seats.aero quota
    assert _node(graph, "sweep:second")["endpoint_source"] == {
        "field": "dest",
        "union": ["SFO"],
        "override": {"dests": ["LHR"]},
    }


def test_stored_v2_program_sweeps_only_rejected_loudly(ready: Path) -> None:
    # program_sweeps was a top-level v2 plan key; a stored doc carrying only it must reject like the
    # other v2 keys, not fall through to the generic empty-legs error.
    trips.new(SLUG)
    path = trips._trip_json(SLUG)
    doc = json.loads(path.read_text())
    doc["plan"] = {"program_sweeps": [{"source": "aeroplan", "dest_region": "asia"}]}
    path.write_text(json.dumps(doc))
    with pytest.raises(UsageError, match="v2 keys") as exc:
        trips.show(SLUG)
    message = str(exc.value)
    assert "program_sweeps" in message
    assert "trip set" in message


def test_leg_after_discover_requires_explicit_origins(ready: Path) -> None:
    # A discover leg is inert until P3, so it can't anchor a successor: the next leg must declare
    # explicit origins or the plan is rejected loudly.
    plan = {
        "legs": [
            {"origins": ["SFO"], "dests": {"discover": {"region": "asia"}}, "mode": "award"},
            {"id": "next", "dests": ["OKA"], "mode": "award"},
        ]
    }
    trips.new(SLUG)
    with pytest.raises(UsageError, match="must declare explicit origins"):
        trips.set_patch(SLUG, {"plan": plan})


def test_leg_after_discover_with_explicit_origins_accepted(ready: Path) -> None:
    plan = {
        "legs": [
            {"origins": ["SFO"], "dests": {"discover": {"region": "asia"}}, "mode": "award"},
            {"id": "next", "origins": ["NRT"], "dests": ["OKA"], "mode": "award"},
        ]
    }
    graph = _graph(plan)
    node = _node(graph, "sweep:next")
    assert node["endpoint_source"] is None  # a fresh chain start, anchored on its own origins
    assert node["inputs"] == []  # nothing carried across the discover gap


def test_mid_plan_discover_breaks_the_chain(ready: Path) -> None:
    # A discover leg mid-plan is a chain BREAK: its successor gets no chain source and no spurious
    # dependency on the pre-gap shortlist — it anchors on its own declared origins (P3 re-anchors).
    plan = {
        "legs": [
            {"origins": ["SFO"], "dests": ["NRT"], "mode": "award"},
            {"id": "scout", "origins": ["NRT"], "dests": {"discover": {}}, "mode": "award"},
            {"id": "after", "origins": ["BKK"], "dests": ["SIN"], "mode": "award"},
        ]
    }
    graph = _graph(plan)
    node = _node(graph, "sweep:after")
    assert node["endpoint_source"] is None  # not chained past the discover leg
    assert node["inputs"] == []  # no dependency on legs/outbound/shortlist.json across the gap
    assert "sweep:scout" not in _ids(graph)  # discover emits no retrieval node


def test_leg_window_requires_start_before_end(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="window.start must be <= end"):
        trips.set_patch(
            SLUG,
            {
                "plan": {
                    "legs": [
                        {
                            "origins": ["SFO"],
                            "dests": ["NRT"],
                            "mode": "award",
                            "window": {"start": "2026-09-14", "end": "2026-09-01"},
                        }
                    ]
                }
            },
        )


@pytest.mark.parametrize(
    "start,end,field",
    [
        pytest.param("2026-09-01T00:00:00+00:00", "2026-09-05", "start", id="tz-aware-start"),
        pytest.param("2026-09-01", "2026-09-05T00:00:00", "end", id="datetime-end"),
        pytest.param("2026-09-01T12:00:00Z", "2026-09-05", "start", id="zulu-start"),
    ],
)
def test_leg_window_rejects_datetime_or_tz_forms(
    ready: Path, start: str, end: str, field: str
) -> None:
    # A datetime/tz-offset window value parses as ISO but a naive-vs-aware pair TypeErrors the
    # start<=end gate; _iso_date pins the shape to YYYY-MM-DD so the gate only sees naive dates.
    trips.new(SLUG)
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "dests": ["NRT"],
                "mode": "award",
                "window": {"start": start, "end": end},
            }
        ]
    }
    with pytest.raises(UsageError, match=f"window.{field} must be a YYYY-MM-DD ISO date"):
        trips.set_patch(SLUG, {"plan": plan})


def test_cross_leg_window_order_rejected(ready: Path) -> None:
    # A later leg whose window ends before an earlier leg's begins is an impossible itinerary.
    trips.new(SLUG)
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "dests": ["NRT"],
                "mode": "award",
                "window": {"start": "2026-09-10", "end": "2026-09-12"},
            },
            {
                "id": "return",
                "dests": "$origins",
                "mode": "award",
                "window": {"start": "2026-09-01", "end": "2026-09-05"},
            },
        ]
    }
    with pytest.raises(UsageError, match="must run forward in time"):
        trips.set_patch(SLUG, {"plan": plan})


def test_forward_windows_accepted(ready: Path) -> None:
    # Windows that advance (or a later leg overlapping a still-open earlier one) are fine.
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "dests": ["NRT"],
                "mode": "award",
                "window": {"start": "2026-09-01", "end": "2026-09-05"},
            },
            {
                "id": "return",
                "dests": "$origins",
                "mode": "award",
                "window": {"start": "2026-09-08", "end": "2026-09-12"},
            },
        ]
    }
    assert _graph(plan)["trip_type"] == "round_trip"


@pytest.mark.parametrize(
    "leg,message",
    [
        pytest.param({"origins": [], "dests": ["NRT"], "mode": "award"}, "origins", id="origins"),
        pytest.param({"origins": ["SFO"], "dests": [], "mode": "award"}, "dests", id="dests"),
        pytest.param(
            {"origins": ["SFO"], "mode": "award", "buckets": [{"name": "asia", "dests": []}]},
            "buckets.dests",
            id="bucket-dests",
        ),
    ],
)
def test_empty_list_endpoints_rejected(ready: Path, leg: dict, message: str) -> None:
    # An empty origins/dests/bucket-dests list is a degenerate endpoint: reject at validation so no
    # $origins anchor or sweep resolves to [].
    trips.new(SLUG)
    with pytest.raises(UsageError, match=f"{message}.*non-empty"):
        trips.set_patch(SLUG, {"plan": {"legs": [leg]}})


def test_either_leg_forwards_bucket_dests_to_successor_union(ready: Path) -> None:
    # An either-mode leg declaring its dests only through buckets forwards those cash-reachable
    # landings into the next leg's chain union; program_sweeps (regions, not dests) forward nothing.
    plan = {
        "legs": [
            {
                "origins": ["SFO"],
                "mode": "either",
                "buckets": [{"name": "asia", "dests": ["NRT", "HND"]}],
            },
            {"id": "return", "dests": "$origins", "mode": "award"},
        ]
    }
    graph = _graph(plan)
    assert _node(graph, "sweep:return")["endpoint_source"]["union"] == ["NRT", "HND"]


def test_all_discover_plan_rejected_at_compile(ready: Path) -> None:
    # A plan of nothing but discover legs has no retrieval node to feed expand — reject at compile
    # rather than emit a vacuous expand-only graph.
    plan = {"legs": [{"origins": ["SFO"], "dests": {"discover": {}}, "mode": "award"}]}
    trips.new(SLUG)
    trips.set_patch(SLUG, {"plan": plan})
    with pytest.raises(UsageError, match="no retrieval-capable legs"):
        trips.compile_graph(SLUG)


def test_shape_label_rejects_legless_plan(ready: Path) -> None:
    # finalize reaches _shape_label without compile's non-empty guard: a legless plan must raise a
    # typed UsageError, never a raw KeyError.
    with pytest.raises(UsageError, match="plan.legs must be a non-empty list"):
        trips._shape_label({})


def test_targets_origins_rejects_legless_plan(ready: Path) -> None:
    # fit/journeys call _targets_origins directly on stored plans; a legless plan must raise the
    # typed compile guard, never a raw KeyError from plan["legs"][-1].
    with pytest.raises(UsageError, match="plan.legs must be a non-empty list before compiling"):
        trips._targets_origins({})


def test_open_jaw_return_override_rides_the_sweep_node(ready: Path) -> None:
    # An explicit non-$origins home with explicit origins is an open jaw: both ride endpoint_source.
    plan = {
        "legs": [
            OUTBOUND,
            {"id": "return", "origins": ["KIX"], "dests": ["SFO"], "mode": "award"},
        ]
    }
    graph = _graph(plan)
    assert graph["trip_type"] == "open_jaw"
    assert _node(graph, "sweep:return")["endpoint_source"]["override"] == {
        "origins": ["KIX"],
        "dests": ["SFO"],
    }


def test_node_routing_runners_are_sonnet_research_is_opus(ready: Path) -> None:
    graph = _graph(CANONICAL)
    assert _node(graph, "sweep:outbound:asia")["routing"] == {"model": "sonnet", "effort": "low"}
    assert _node(graph, "assess")["routing"] == {"model": "opus", "effort": "xhigh"}


def test_explain_flags_node_freshness(
    ready: Path, frozen_clock: Callable[[], dt.datetime]
) -> None:
    _graph({"legs": [OUTBOUND]})
    graph = trips.explain(SLUG, now=frozen_clock)
    assert all(n["fresh"] is False for n in graph["nodes"])  # nothing has run yet
