import datetime as dt
import json
import types
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from getaway import awardwallet, keys, paths, prefs, registry
from getaway.awardwallet import AuthError, AwardWalletClient, normalize_account
from getaway.constants import EXIT_AUTH, EXIT_NO_DATA, EXIT_OK

FIXTURES = Path(__file__).parent / "fixtures"
FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
USERS_URL = f"{awardwallet.BASE_URL}/connectedUser"
ACCOUNT_URL = f"{awardwallet.BASE_URL}/account"
PROVIDERS_URL = f"{awardwallet.BASE_URL}/providers/list"

SECOND_USER_DETAIL = {
    "userId": 46,
    "fullName": "Priya Mohamedali",
    "email": "priya@example.com",
    "accounts": [{"accountId": 8001, "code": "jetblue", "displayName": "JetBlue TrueBlue"}],
}
JETBLUE_DETAIL = {
    "account": {
        "accountId": 8001,
        "code": "jetblue",
        "displayName": "JetBlue TrueBlue",
        "kind": "Airlines",
        "owner": "Priya Mohamedali",
        "balance": "61,204",
        "balanceRaw": 61204,
        "errorCode": 0,
        "lastRetrieveDate": "2026-07-12T12:00:00+00:00",
        "properties": [],
    }
}
ACCOUNT_VARIANTS = ((7001, "healthy"), (7002, "errored"), (7003, "bank"), (7004, "unmapped"))


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def account_variant(name: str) -> dict:
    return load("awardwallet_account.json")[name]["account"]


def _mock_pull_routes() -> dict[str, respx.Route]:
    accounts = load("awardwallet_account.json")
    routes = {
        "users": respx.get(USERS_URL).mock(
            return_value=httpx.Response(200, json=load("awardwallet_users.json"))
        ),
        "user45": respx.get(f"{USERS_URL}/45").mock(
            return_value=httpx.Response(200, json=load("awardwallet_user.json"))
        ),
        "user46": respx.get(f"{USERS_URL}/46").mock(
            return_value=httpx.Response(200, json=SECOND_USER_DETAIL)
        ),
        "8001": respx.get(f"{ACCOUNT_URL}/8001").mock(
            return_value=httpx.Response(200, json=JETBLUE_DETAIL)
        ),
    }
    for account_id, variant in ACCOUNT_VARIANTS:
        routes[str(account_id)] = respx.get(f"{ACCOUNT_URL}/{account_id}").mock(
            return_value=httpx.Response(200, json=accounts[variant])
        )
    return routes


def _write_prefs_awardwallet_op_ref(ref: str) -> None:
    prefs.init()
    prefs.set_patch({"awardwallet_op_ref": ref})


def test_env_key_wins_without_a_prefs_file(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(awardwallet.API_KEY_ENV, "aw-env-secret")
    assert not paths.prefs_path().exists()
    assert awardwallet.resolve_api_key() == "aw-env-secret"


def test_missing_credentials_raise_auth_error(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(awardwallet.API_KEY_ENV, raising=False)
    with pytest.raises(AuthError):
        awardwallet.resolve_api_key()


def test_awardwallet_op_ref_resolves_via_op_read(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(awardwallet.API_KEY_ENV, raising=False)
    _write_prefs_awardwallet_op_ref("op://Vault/awardwallet/credential")
    captured: dict[str, list[str]] = {}

    def _fake_run(argv: list[str], **_kwargs: object) -> object:
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="aw_resolved_secret\n", stderr="")

    monkeypatch.setattr(keys.subprocess, "run", _fake_run)
    assert awardwallet.resolve_api_key() == "aw_resolved_secret"
    assert captured["argv"] == ["op", "read", "op://Vault/awardwallet/credential"]


def test_seats_op_ref_does_not_resolve_awardwallet(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(awardwallet.API_KEY_ENV, raising=False)
    prefs.init()
    prefs.set_patch({"op_ref": "op://Vault/seats/credential"})

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("op must not run for another service's reference")

    monkeypatch.setattr(keys.subprocess, "run", _forbidden)
    with pytest.raises(AuthError):
        awardwallet.resolve_api_key()


def test_injected_malformed_key_rejected_without_leaking() -> None:
    bad_key = "aw_valid\r\nX-Bad: yes"
    with pytest.raises(AuthError) as excinfo:
        AwardWalletClient(api_key=bad_key)
    message = str(excinfo.value)
    assert bad_key not in message
    assert "X-Bad" not in message


@respx.mock
def test_requests_carry_auth_header_and_exact_base_url() -> None:
    respx.get(USERS_URL).mock(return_value=httpx.Response(200, json={"connectedUsers": []}))
    AwardWalletClient(api_key="test-key").users()
    request = respx.calls.last.request
    assert str(request.url) == "https://business.awardwallet.com/api/export/v1/connectedUser"
    assert request.headers["X-Authentication"] == "test-key"


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
def test_auth_status_raises_auth_error_without_key(status: int) -> None:
    respx.get(USERS_URL).mock(return_value=httpx.Response(status, json={"error": "denied"}))
    with pytest.raises(AuthError) as excinfo:
        AwardWalletClient(api_key="test-key").users()
    assert "test-key" not in str(excinfo.value)


@respx.mock
def test_non_2xx_raises() -> None:
    respx.get(USERS_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(httpx.HTTPStatusError):
        AwardWalletClient(api_key="test-key").users()


@respx.mock
def test_pull_walks_users_then_accounts_then_details() -> None:
    routes = _mock_pull_routes()
    result = AwardWalletClient(api_key="test-key").pull(now=lambda: FROZEN)
    assert routes["users"].call_count == 1
    assert routes["user45"].call_count == 1
    assert routes["user46"].call_count == 1
    for account_id in ("7001", "7002", "7003", "7004", "8001"):
        assert routes[account_id].call_count == 1
    assert [row["aw_account_id"] for row in result["rows"]] == [7001, 7002, 7003, 7004, 8001]
    assert result["users"] == [
        {"user_id": 45, "name": "Yasyf Mohamedali"},
        {"user_id": 46, "name": "Priya Mohamedali"},
    ]


@respx.mock
def test_pull_user_filter_never_fetches_other_users_accounts() -> None:
    routes = _mock_pull_routes()
    result = AwardWalletClient(api_key="test-key").pull(now=lambda: FROZEN, user_ids=[45])
    assert routes["user46"].call_count == 0
    assert routes["8001"].call_count == 0
    assert result["users"] == [{"user_id": 45, "name": "Yasyf Mohamedali"}]
    assert [row["aw_account_id"] for row in result["rows"]] == [7001, 7002, 7003, 7004]


def test_normalize_healthy_full_row_uses_balance_raw() -> None:
    row = normalize_account(account_variant("healthy"), registry.awardwallet_map(), FROZEN)
    assert row == {
        "slug": "aeroplan",
        "bucket": "programs",
        "aw_code": "aeroplan",
        "aw_account_id": 7001,
        "display_name": "Air Canada Aeroplan",
        "aw_kind": "Airlines",
        "owner": "Yasyf Mohamedali",
        "balance": 88450,
        "tier": "50K",
        "tier_rank": 3,
        "expiration": "2027-12-31T00:00:00+00:00",
        "status_expiration": "2027-01-31",
        "last_retrieved": "2026-07-11T12:00:00+00:00",
        "last_change": "2026-07-10T09:15:00+00:00",
        "age_days": 2,
        "error_code": 0,
    }


@pytest.mark.parametrize(
    ("variant", "tier", "tier_rank"),
    [("healthy", "50K", 3), ("bank", None, None)],
    ids=["kind-3-present", "kind-3-absent"],
)
def test_tier_and_rank_come_from_kind_3(
    variant: str, tier: str | None, tier_rank: int | None
) -> None:
    row = normalize_account(account_variant(variant), registry.awardwallet_map(), FROZEN)
    assert row["tier"] == tier
    assert row["tier_rank"] == tier_rank


@pytest.mark.parametrize(
    ("variant", "status_expiration", "expiration"),
    [("healthy", "2027-01-31", "2027-12-31T00:00:00+00:00"), ("errored", None, None)],
    ids=["present", "absent"],
)
def test_status_expiration_from_kind_15_and_expiration_verbatim(
    variant: str, status_expiration: str | None, expiration: str | None
) -> None:
    row = normalize_account(account_variant(variant), registry.awardwallet_map(), FROZEN)
    assert row["status_expiration"] == status_expiration
    assert row["expiration"] == expiration


def test_error_code_is_data_not_an_exception() -> None:
    row = normalize_account(account_variant("errored"), registry.awardwallet_map(), FROZEN)
    assert row["error_code"] == 2
    assert row["balance"] is None
    assert row["slug"] == "alaska"


@pytest.mark.parametrize(
    ("variant", "age_days"),
    [("healthy", 2), ("errored", None)],
    ids=["retrieved-2-days-ago", "never-retrieved"],
)
def test_age_days_against_frozen_clock(variant: str, age_days: int | None) -> None:
    row = normalize_account(account_variant(variant), registry.awardwallet_map(), FROZEN)
    assert row["age_days"] == age_days


@pytest.mark.parametrize(
    ("variant", "slug", "bucket"),
    [
        ("healthy", "aeroplan", "programs"),
        ("bank", "amex", "transferable"),
        ("unmapped", None, None),
    ],
    ids=["program", "bank", "unmapped"],
)
def test_slug_and_bucket_from_registry(variant: str, slug: str | None, bucket: str | None) -> None:
    row = normalize_account(account_variant(variant), registry.awardwallet_map(), FROZEN)
    assert row["slug"] == slug
    assert row["bucket"] == bucket


@respx.mock
def test_pull_command_emits_users_and_rows(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(awardwallet.API_KEY_ENV, "aw_test")
    _mock_pull_routes()
    result = CliRunner().invoke(awardwallet.pull_cmd, [])
    assert result.exit_code == EXIT_OK
    payload = json.loads(result.output)
    assert payload["users"] == [
        {"user_id": 45, "name": "Yasyf Mohamedali"},
        {"user_id": 46, "name": "Priya Mohamedali"},
    ]
    assert [row["aw_account_id"] for row in payload["rows"]] == [7001, 7002, 7003, 7004, 8001]
    assert payload["rows"][0]["slug"] == "aeroplan"
    assert payload["rows"][0]["balance"] == 88450


@respx.mock
def test_pull_command_zero_users_exits_no_data(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(awardwallet.API_KEY_ENV, "aw_test")
    respx.get(USERS_URL).mock(return_value=httpx.Response(200, json={"connectedUsers": []}))
    result = CliRunner().invoke(awardwallet.pull_cmd, [])
    assert result.exit_code == EXIT_NO_DATA


def test_pull_command_without_credentials_exits_auth(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(awardwallet.API_KEY_ENV, raising=False)
    result = CliRunner().invoke(awardwallet.pull_cmd, [])
    assert result.exit_code == EXIT_AUTH


@respx.mock
def test_providers_command_passthrough(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(awardwallet.API_KEY_ENV, "aw_test")
    providers = [
        {"code": "aeroplan", "displayName": "Air Canada Aeroplan", "kind": "Airlines"},
        {
            "code": "membershiprewards",
            "displayName": "American Express Membership Rewards",
            "kind": "Credit Cards",
        },
    ]
    respx.get(PROVIDERS_URL).mock(return_value=httpx.Response(200, json=providers))
    result = CliRunner().invoke(awardwallet.providers_cmd, [])
    assert result.exit_code == EXIT_OK
    assert json.loads(result.output) == {"providers": providers, "count": 2}
