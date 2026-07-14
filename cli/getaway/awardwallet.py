from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Callable, Sequence
from typing import Any

import click
import httpx

from getaway import keys, registry
from getaway.constants import EXIT_AUTH, EXIT_NO_DATA
from getaway.keys import AuthError
from getaway.paths import emit, utcnow

BASE_URL = "https://business.awardwallet.com/api/export/v1"
AUTH_HEADER = "X-Authentication"
API_KEY_ENV = "AWARDWALLET_API_KEY"
PREFS_KEY = "awardwallet_op_ref"
HTTP_TIMEOUT = httpx.Timeout(30.0)
ELITE_TIER_KIND = 3
STATUS_EXPIRATION_KIND = 15

Row = dict[str, Any]


class AwardWalletError(RuntimeError):
    """A malformed AwardWallet response."""


def _require_int_id(value: object, label: str) -> int:
    # The documented schema types these ids as integers; a non-int (e.g. a crafted path-traversal
    # string) must never reach the URL path we build from it.
    if not isinstance(value, int) or isinstance(value, bool):
        raise AwardWalletError(f"AwardWallet {label} must be an integer, got {value!r}")
    return value


def _property(raw: Row, kind: int) -> Row | None:
    return next((p for p in raw.get("properties") or [] if p.get("kind") == kind), None)


def _bucket(slug: str | None) -> str | None:
    if slug is None:
        return None
    return "programs" if registry.is_program(slug) else "transferable"


def _age_days(last_retrieved: str | None, now: dt.datetime) -> int | None:
    if last_retrieved is None:
        return None
    parsed = dt.datetime.fromisoformat(last_retrieved)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return (now - parsed).days


def normalize_account(raw: Row, code_map: dict[str, str], now: dt.datetime) -> Row:
    """Project one AwardWallet account detail onto the getaway balance-row shape.

    ``balance`` comes from ``balanceRaw`` only; the display string is never
    parsed. A nonzero ``errorCode`` is data, not an exception. ``subAccounts``
    and ``history`` are deliberately ignored.
    """
    slug = code_map.get(raw["code"])
    tier = _property(raw, ELITE_TIER_KIND)
    status_expiration = _property(raw, STATUS_EXPIRATION_KIND)
    last_retrieved = raw.get("lastRetrieveDate")
    return {
        "slug": slug,
        "bucket": _bucket(slug),
        "aw_code": raw["code"],
        "aw_account_id": raw["accountId"],
        "display_name": raw["displayName"],
        "aw_kind": raw["kind"],
        "owner": raw.get("owner"),
        "balance": raw.get("balanceRaw"),
        "tier": tier["value"] if tier else None,
        "tier_rank": tier.get("rank") if tier else None,
        "expiration": raw.get("expirationDate"),
        "status_expiration": status_expiration["value"] if status_expiration else None,
        "last_retrieved": last_retrieved,
        "last_change": raw.get("lastChangeDate"),
        "age_days": _age_days(last_retrieved, now),
        "error_code": raw["errorCode"],
        "error_message": raw.get("errorMessage"),
    }


def resolve_api_key() -> str:
    return keys.resolve(API_KEY_ENV, PREFS_KEY, "awardwallet")


class AwardWalletClient:
    def __init__(self, api_key: str | None = None) -> None:
        if api_key is not None:
            key = keys.validate(api_key, "awardwallet")
        else:
            key = resolve_api_key()
        self._client = httpx.Client(headers={AUTH_HEADER: key}, timeout=HTTP_TIMEOUT)

    def users(self) -> list[Row]:
        """Connected users visible to the API credential."""
        return self._get("/connectedUser")["connectedUsers"]

    def user_accounts(self, user_id: int) -> Row:
        """One connected user's detail, including its accounts array."""
        _require_int_id(user_id, "userId")
        return self._get(f"/connectedUser/{user_id}")

    def account(self, account_id: int) -> Row:
        """Full detail for one loyalty account."""
        _require_int_id(account_id, "accountId")
        return self._get(f"/account/{account_id}")["account"]

    def providers(self) -> list[Row]:
        """Every loyalty provider AwardWallet can track."""
        return self._get("/providers/list")

    def pull(
        self,
        now: Callable[[], dt.datetime] = utcnow,
        user_ids: Sequence[int] | None = None,
    ) -> Row:
        """Walk connected users to per-account detail, one request at a time."""
        code_map = registry.awardwallet_map()
        moment = now()
        users = self.users()
        if user_ids is not None:
            wanted = set(user_ids)
            users = [user for user in users if user["userId"] in wanted]
        rows = [
            normalize_account(self.account(entry["accountId"]), code_map, moment)
            for user in users
            for entry in self.user_accounts(user["userId"])["accounts"]
        ]
        return {
            "users": [{"user_id": user["userId"], "name": user["fullName"]} for user in users],
            "rows": rows,
        }

    def _get(self, path: str) -> Any:
        response = self._client.get(f"{BASE_URL}{path}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            if response.status_code in (401, 403):
                raise AuthError("awardwallet rejected the API credential") from err
            raise
        return response.json()


def _map_errors(fn: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except AuthError as err:
            click.echo(str(err), err=True)
            raise SystemExit(EXIT_AUTH) from err

    return wrapper


awardwallet_group = click.Group("awardwallet", help="AwardWallet-connected loyalty balances.")


@awardwallet_group.command("pull")
@click.option("--user", "user_ids", multiple=True, type=int)
@_map_errors
def pull_cmd(user_ids: tuple[int, ...]) -> None:
    """Emit every connected user's accounts as normalized balance rows."""
    result = AwardWalletClient().pull(user_ids=list(user_ids) or None)
    if not result["users"]:
        click.echo("no connected awardwallet users", err=True)
        raise SystemExit(EXIT_NO_DATA)
    if not result["rows"]:
        click.echo("no awardwallet accounts", err=True)
        raise SystemExit(EXIT_NO_DATA)
    emit(result)


@awardwallet_group.command("providers")
@_map_errors
def providers_cmd() -> None:
    """List every provider AwardWallet can track."""
    rows = AwardWalletClient().providers()
    emit({"providers": rows, "count": len(rows)})


@awardwallet_group.command("account")
@click.argument("account_id", type=int)
@_map_errors
def account_cmd(account_id: int) -> None:
    """Emit one account's raw AwardWallet detail."""
    emit(AwardWalletClient().account(account_id))
