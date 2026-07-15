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


def test_onward_dests_veto_binds_endpoints(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    trips.new(SLUG)
    trips.set_patch(SLUG, {"avoid_final_destinations": ["NRT"]})
    with pytest.raises(UsageError, match="vetoed"):
        trips.set_patch(SLUG, {"plan": {"hybrid": {"onward_dests": ["ICN"]}}})
    with pytest.raises(UsageError, match="vetoed"):
        trips.set_patch(SLUG, {"plan": {"hybrid": {"onward_dests": ["NRT"]}}})
    ok = trips.set_patch(SLUG, {"plan": {"hybrid": {"onward_dests": ["BKK"]}}})
    assert ok["plan"]["hybrid"]["onward_dests"] == ["BKK"]


def test_gateways_not_vetoed_by_avoid_lists(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    trips.new(SLUG)
    doc = trips.set_patch(
        SLUG, {"plan": {"hybrid": {"gateways": ["ICN"], "onward_dests": ["BKK"]}}}
    )
    assert doc["plan"]["hybrid"]["gateways"] == ["ICN"]


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
        trips.set_patch(SLUG, {"plan": {"hybrid": {"unexpected": 1}}})


@pytest.mark.parametrize(
    "origins",
    [
        pytest.param("WST", id="bare-string"),
        pytest.param([1], id="non-string-member"),
    ],
)
def test_plan_origins_shape_validated(ready: Path, origins: object) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="plan.origins"):
        trips.set_patch(SLUG, {"plan": {"origins": origins}})


def test_plan_origins_string_list_accepted(ready: Path) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(SLUG, {"plan": {"origins": ["WST"]}})
    assert doc["plan"]["origins"] == ["WST"]


def test_explicit_plan_origins_are_unchanged_by_preferences(ready: Path) -> None:
    prefs.set_patch({"home_airport": "SEA", "origin_airports": ["SFO", "OAK"]})
    trips.new(SLUG)
    trips.set_patch(SLUG, {"plan": {"trip_type": "one_way", "origins": ["WST"]}})

    assert trips.show(SLUG)["plan"]["origins"] == ["WST"]


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
def test_omitted_plan_origins_resolve_from_preferences(
    ready: Path, pref_patch: dict, expected: list[str]
) -> None:
    prefs.set_patch(pref_patch)
    trips.new(SLUG)
    trips.set_patch(SLUG, {"plan": {"trip_type": "one_way"}})

    assert trips.show(SLUG)["plan"]["origins"] == expected
    assert json.loads((trips.trip_dir(SLUG) / "trip.json").read_text())["plan"] == {
        "trip_type": "one_way"
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
    trips.set_patch(SLUG, {"plan": {"trip_type": "one_way", "origins": ["WST"]}})
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
def test_plan_bucket_name_validated(ready: Path, name: str) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": {"buckets": [{"name": name, "dests": ["NRT"]}]}})


def test_plan_bucket_valid_name_accepted(ready: Path) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(SLUG, {"plan": {"buckets": [{"name": "asia-1", "dests": ["NRT"]}]}})
    assert doc["plan"]["buckets"] == [{"name": "asia-1", "dests": ["NRT"]}]


def test_plan_bucket_shape_validated(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": {"buckets": [{"name": "asia"}]}})  # missing dests
    with pytest.raises(UsageError):
        trips.set_patch(SLUG, {"plan": {"buckets": [{"name": "asia", "dests": "NRT"}]}})


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


@pytest.mark.parametrize("trip_type", ["one_way", "round_trip"])
def test_trip_type_variants_accepted(ready: Path, trip_type: str) -> None:
    trips.new(SLUG)
    doc = trips.set_patch(SLUG, {"plan": {"trip_type": trip_type}})
    assert doc["plan"]["trip_type"] == trip_type


def test_trip_type_invalid_rejected(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="trip_type"):
        trips.set_patch(SLUG, {"plan": {"trip_type": "multi_city"}})


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


def test_return_override_origins_veto_checked(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["ICN"]})
    trips.new(SLUG)
    with pytest.raises(UsageError, match="vetoed"):
        trips.set_patch(SLUG, {"plan": {"trip_type": "round_trip", "return": {"origins": ["ICN"]}}})


def test_return_override_home_dests_exempt(ready: Path) -> None:
    prefs.set_patch({"avoid_destinations": ["SFO"]})  # even a vetoed home is a valid return dest
    trips.new(SLUG)
    ret = {"origins": ["KIX"], "dests": ["SFO"]}
    doc = trips.set_patch(SLUG, {"plan": {"trip_type": "round_trip", "return": ret}})
    assert doc["plan"]["return"] == ret


def test_return_rejected_for_one_way(ready: Path) -> None:
    trips.new(SLUG)
    with pytest.raises(UsageError, match="one-way"):
        trips.set_patch(SLUG, {"plan": {"trip_type": "one_way", "return": {"origins": ["KIX"]}}})


def test_lodging_must_be_object(ready: Path) -> None:
    trips.new(SLUG)
    assert trips.set_patch(SLUG, {"plan": {"lodging": {}}})["plan"]["lodging"] == {}
    with pytest.raises(UsageError, match="lodging"):
        trips.set_patch(SLUG, {"plan": {"lodging": "a week somewhere warm"}})


def test_resume_lists_expiring_instruments_across_variants(
    ready: Path, frozen_clock: Callable[[], dt.datetime]
) -> None:
    # resume() reads only the union's shared type + expires fields, so every instrument variant
    # renders (the credits→travel_instruments cutover consumer).
    trips.new(SLUG, now=frozen_clock)
    window = {"start": "2026-09-01", "end": "2026-09-14", "trip_length_days": 10}
    plan = {"trip_type": "one_way", "origins": ["SFO"]}
    plan["buckets"] = [{"name": "a", "dests": ["NRT"]}]
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
