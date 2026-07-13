import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from getaway import prefs
from getaway.paths import StateConflictError, UsageError


@pytest.fixture
def ready(getaway_home: Path) -> Path:
    prefs.init()
    return getaway_home


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_init_writes_neutral_template(getaway_home: Path) -> None:
    doc = prefs.init()
    assert doc == {
        "op_ref": None,
        "home_airport": None,
        "origin_airports": [],
        "avoid_transit": [],
        "avoid_destinations": [],
        "departure_days": [],
        "avoid_airlines": [],
        "layovers": {
            "style": "minimize",
            "min_connection_minutes": 75,
            "prefer_cities": [],
            "avoid_cities": [],
        },
        "statuses": {},
        "status_goals": [],
        "balances": {"programs": {}, "transferable": {}},
        "credits": [],
        "documents": {"passports": [], "residency": [], "visas": []},
    }
    assert json.loads(prefs.prefs_path().read_text()) == doc


def test_init_refuses_when_already_initialized(ready: Path) -> None:
    with pytest.raises(StateConflictError):
        prefs.init()


def test_init_cli_exits_state_conflict_on_reinit(ready: Path, runner: CliRunner) -> None:
    result = runner.invoke(prefs.prefs_group, ["init"])
    assert result.exit_code == 3


def test_show_raises_when_uninitialized(getaway_home: Path) -> None:
    with pytest.raises(StateConflictError):
        prefs.show()


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        pytest.param(lambda: None, False, id="empty-not-configured"),
        pytest.param(lambda: prefs.set_balance("aeroplan", 50000), True, id="program-configured"),
        pytest.param(lambda: prefs.set_balance("amex", 120000), True, id="transferable-configured"),
    ],
)
def test_configured_predicate(ready: Path, setup: Callable[[], object], expected: bool) -> None:
    setup()
    assert prefs.configured() is expected


def test_status_cli_exit_codes(ready: Path, runner: CliRunner) -> None:
    unconfigured = runner.invoke(prefs.prefs_group, ["status"])
    assert unconfigured.exit_code == 1
    assert json.loads(unconfigured.stdout) == {"configured": False}
    prefs.set_balance("aeroplan", 50000)
    configured = runner.invoke(prefs.prefs_group, ["status"])
    assert configured.exit_code == 0
    assert json.loads(configured.stdout) == {"configured": True}


@pytest.mark.parametrize(
    ("patch", "key", "value"),
    [
        pytest.param({"op_ref": "op://vault/item"}, "op_ref", "op://vault/item", id="op-ref"),
        pytest.param({"home_airport": "SFO"}, "home_airport", "SFO", id="home-airport"),
        pytest.param(
            {"origin_airports": ["SFO", "OAK"]}, "origin_airports", ["SFO", "OAK"], id="origins"
        ),
        pytest.param(
            {"departure_days": ["Fri", "Sat"]},
            "departure_days",
            ["Fri", "Sat"],
            id="departure-days",
        ),
        pytest.param(
            {"avoid_airlines": [{"code": "UA", "name": "United", "strength": "soft"}]},
            "avoid_airlines",
            [{"code": "UA", "name": "United", "strength": "soft"}],
            id="avoid-airlines",
        ),
        pytest.param(
            {
                "layovers": {
                    "style": "explore",
                    "min_connection_minutes": 90,
                    "prefer_cities": ["IST"],
                    "avoid_cities": ["LHR"],
                }
            },
            "layovers",
            {
                "style": "explore",
                "min_connection_minutes": 90,
                "prefer_cities": ["IST"],
                "avoid_cities": ["LHR"],
            },
            id="layovers-explore",
        ),
        pytest.param(
            {"status_goals": [{"program": "united", "target": "1K", "by": "2026-12-31"}]},
            "status_goals",
            [{"program": "united", "target": "1K", "by": "2026-12-31"}],
            id="status-goals",
        ),
        pytest.param(
            {"documents": {"passports": ["US"], "residency": [], "visas": []}},
            "documents",
            {"passports": ["US"], "residency": [], "visas": []},
            id="documents",
        ),
    ],
)
def test_set_patch_merges_valid(
    ready: Path, patch: dict, key: str, value: object
) -> None:
    result = prefs.set_patch(patch)
    assert result[key] == value
    # untouched keys keep their template defaults
    assert result["balances"] == {"programs": {}, "transferable": {}}
    assert json.loads(prefs.prefs_path().read_text())[key] == value


def test_set_patch_top_level_merge_accumulates(ready: Path) -> None:
    prefs.set_patch({"home_airport": "SFO"})
    prefs.set_patch({"op_ref": "op://vault/item"})
    doc = prefs.show()
    assert doc["home_airport"] == "SFO"
    assert doc["op_ref"] == "op://vault/item"


def test_set_patch_requires_initialized(getaway_home: Path) -> None:
    with pytest.raises(StateConflictError):
        prefs.set_patch({"home_airport": "SFO"})


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param({"nope": 1}, id="unknown-top-level-key"),
        pytest.param({"op_ref": 5}, id="op-ref-not-string"),
        pytest.param({"home_airport": 5}, id="home-airport-not-string"),
        pytest.param({"origin_airports": "SFO"}, id="origins-not-list"),
        pytest.param({"origin_airports": [5]}, id="origins-non-string-element"),
        pytest.param({"departure_days": ["Funday"]}, id="departure-day-bad-token"),
        pytest.param(
            {"avoid_airlines": [{"code": "UA", "name": "United"}]}, id="avoid-airline-missing-key"
        ),
        pytest.param(
            {"avoid_airlines": [{"code": "UA", "name": "United", "strength": "medium"}]},
            id="avoid-airline-bad-strength",
        ),
        pytest.param(
            {"avoid_airlines": [{"code": "UA", "name": "United", "strength": "soft", "x": 1}]},
            id="avoid-airline-extra-key",
        ),
        pytest.param(
            {
                "layovers": {
                    "style": "wander",
                    "min_connection_minutes": 75,
                    "prefer_cities": [],
                    "avoid_cities": [],
                }
            },
            id="layovers-bad-style",
        ),
        pytest.param({"layovers": {"style": "minimize"}}, id="layovers-missing-keys"),
        pytest.param(
            {
                "layovers": {
                    "style": "minimize",
                    "min_connection_minutes": "75",
                    "prefer_cities": [],
                    "avoid_cities": [],
                }
            },
            id="layovers-min-connection-not-int",
        ),
        pytest.param({"statuses": {"united": 5}}, id="status-tier-not-string"),
        pytest.param(
            {"status_goals": [{"program": "united", "target": "1K"}]}, id="status-goal-missing-key"
        ),
        pytest.param({"balances": {"programs": {}}}, id="balances-missing-transferable"),
        pytest.param(
            {
                "credits": [
                    {
                        "id": "x",
                        "kind": "bogus",
                        "issuer": "Delta",
                        "amount": 200,
                        "currency": "USD",
                        "expires": "2026-08-01",
                    }
                ]
            },
            id="credit-bad-kind",
        ),
        pytest.param(
            {
                "credits": [
                    {
                        "id": "x",
                        "kind": "voucher",
                        "issuer": "Delta",
                        "amount": 200,
                        "currency": "USD",
                        "expires": "not-a-date",
                    }
                ]
            },
            id="credit-bad-expires",
        ),
        pytest.param(
            {"documents": {"passports": [], "residency": []}}, id="documents-missing-visas"
        ),
        pytest.param(
            {"documents": {"passports": "US", "residency": [], "visas": []}},
            id="documents-section-not-list",
        ),
        pytest.param(
            {"documents": {"passports": [5], "residency": [], "visas": []}},
            id="documents-section-element-not-string",
        ),
        pytest.param(
            {"balances": {"programs": [], "transferable": {}}},
            id="balances-programs-not-dict",
        ),
        pytest.param(
            {"balances": {"programs": {}, "transferable": []}},
            id="balances-transferable-not-dict",
        ),
        pytest.param(
            {"balances": {"programs": {"united": "lots"}, "transferable": {}}},
            id="balances-program-value-not-int",
        ),
        pytest.param(
            {"balances": {"programs": {"united": True}, "transferable": {}}},
            id="balances-program-value-bool-not-int",
        ),
        pytest.param(
            {"balances": {"programs": {}, "transferable": {"amex": 1.5}}},
            id="balances-transferable-value-not-int",
        ),
    ],
)
def test_set_patch_rejects_invalid(ready: Path, patch: dict) -> None:
    with pytest.raises(UsageError):
        prefs.set_patch(patch)


def test_set_patch_cli_rejects_invalid_with_usage_exit(ready: Path, runner: CliRunner) -> None:
    result = runner.invoke(prefs.prefs_group, ["set"], input='{"nope": 1}')
    assert result.exit_code == 64


def test_set_patch_cli_malformed_json_exits_usage(ready: Path, runner: CliRunner) -> None:
    # A stdin body that is not valid JSON maps to a usage error (exit 64), not a raw
    # JSONDecodeError traceback (exit 1).
    result = runner.invoke(prefs.prefs_group, ["set"], input="{not valid json")
    assert result.exit_code == 64


@pytest.mark.parametrize(
    ("slug", "amount", "bucket"),
    [
        pytest.param("aeroplan", 50000, "programs", id="program-slug-routes-to-programs"),
        pytest.param("united", 12345, "programs", id="another-program-slug"),
        pytest.param("amex", 120000, "transferable", id="bank-slug-routes-to-transferable"),
        pytest.param("chase", 80000, "transferable", id="another-bank-slug"),
    ],
)
def test_set_balance_routing(ready: Path, slug: str, amount: int, bucket: str) -> None:
    doc = prefs.set_balance(slug, amount)
    assert doc["balances"][bucket][slug] == amount
    other = "transferable" if bucket == "programs" else "programs"
    assert doc["balances"][other] == {}


def test_set_balance_rejects_unknown_slug(ready: Path) -> None:
    with pytest.raises(UsageError):
        prefs.set_balance("not-a-real-slug", 1000)


def test_set_balance_cli_unknown_slug_exit_usage(ready: Path, runner: CliRunner) -> None:
    result = runner.invoke(prefs.prefs_group, ["set-balance", "not-a-real-slug", "1000"])
    assert result.exit_code == 64


def test_set_status_writes_and_validates(ready: Path) -> None:
    doc = prefs.set_status("united", "1K")
    assert doc["statuses"] == {"united": "1K"}
    with pytest.raises(UsageError):
        prefs.set_status("not-a-program", "gold")


def test_credit_add_generates_short_hex_id(ready: Path) -> None:
    row = prefs.credit_add("voucher", "Delta", 200, "USD", "2026-08-01")
    assert len(row["id"]) == 8
    int(row["id"], 16)  # hex-parseable
    assert prefs.show()["credits"] == [row]


def test_credit_add_rejects_bad_kind(ready: Path) -> None:
    with pytest.raises(UsageError):
        prefs.credit_add("frequent-flyer", "Delta", 200, "USD", "2026-08-01")


def test_credit_remove_lifecycle(ready: Path) -> None:
    a = prefs.credit_add("voucher", "Delta", 200, "USD", "2026-08-01")
    b = prefs.credit_add("certificate", "United", 1, "USD", "2026-09-01")
    prefs.credit_remove(a["id"])
    assert [c["id"] for c in prefs.show()["credits"]] == [b["id"]]
    with pytest.raises(UsageError):
        prefs.credit_remove(a["id"])


def test_credit_list_expiring_within_filters_against_frozen_clock(
    ready: Path, frozen_clock: Callable[[], dt.datetime]
) -> None:
    # frozen clock is 2026-07-13; 90d window is [2026-07-13, 2026-10-11]
    soon = prefs.credit_add("voucher", "Delta", 200, "USD", "2026-07-20")
    today = prefs.credit_add("credit", "United", 50, "USD", "2026-07-13")
    beyond = prefs.credit_add("certificate", "Amex", 1, "USD", "2026-11-01")
    expired = prefs.credit_add("companion", "Alaska", 1, "USD", "2026-06-30")

    within_90 = {c["id"] for c in prefs.credit_list("90d", now=frozen_clock)}
    assert within_90 == {soon["id"], today["id"]}

    within_200 = {c["id"] for c in prefs.credit_list("200d", now=frozen_clock)}
    assert within_200 == {soon["id"], today["id"], beyond["id"]}

    assert expired["id"] not in within_200
    assert len(prefs.credit_list(now=frozen_clock)) == 4


def test_credit_list_rejects_malformed_window(ready: Path) -> None:
    with pytest.raises(UsageError):
        prefs.credit_list("3 months")
