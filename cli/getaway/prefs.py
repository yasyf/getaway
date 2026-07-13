import json
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from importlib import resources

import click

from getaway.paths import (
    NegativePredicate,
    StateConflictError,
    UsageError,
    atomic_update,
    emit,
    map_errors,
    prefs_path,
    require_int,
    require_keys,
    require_str,
    require_str_list,
    require_str_or_none,
    utcnow,
)

LAYOVER_STYLES = frozenset({"minimize", "explore"})
AIRLINE_STRENGTHS = frozenset({"soft", "hard"})
CREDIT_KINDS = frozenset({"voucher", "credit", "certificate", "companion"})
DAY_TOKENS = frozenset({"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})
CREDIT_REQUIRED = frozenset({"id", "kind", "issuer", "amount", "currency", "expires"})
LAYOVER_KEYS = frozenset({"style", "min_connection_minutes", "prefer_cities", "avoid_cities"})
_EXPIRING_RE = re.compile(r"^(\d+)d$")


def _template() -> dict:
    return {
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


TEMPLATE_KEYS = frozenset(_template())


def _load_data(name: str) -> dict:
    return json.loads((resources.files("getaway") / "data" / name).read_text())


def _load() -> dict:
    path = prefs_path()
    if not path.exists():
        raise StateConflictError("preferences not initialized; run prefs init")
    return json.loads(path.read_text())


def _require_initialized(current: dict) -> None:
    if not current:
        raise StateConflictError("preferences not initialized; run prefs init")


def _check_iso_date(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise UsageError(f"{label} must be an ISO date string")
    try:
        date.fromisoformat(value)
    except ValueError as err:
        raise UsageError(f"{label} is not an ISO date: {value!r}") from err


def _validate_credit(row: object, label: str) -> None:
    row = require_keys(row, set(CREDIT_REQUIRED), label, optional=frozenset({"note"}))
    if row["kind"] not in CREDIT_KINDS:
        raise UsageError(f"{label}.kind must be one of {sorted(CREDIT_KINDS)}")
    require_str(row["issuer"], f"{label}.issuer")
    require_str(row["currency"], f"{label}.currency")
    if not isinstance(row["amount"], (int, float)) or isinstance(row["amount"], bool):
        raise UsageError(f"{label}.amount must be a number")
    _check_iso_date(row["expires"], f"{label}.expires")
    if "note" in row:
        require_str(row["note"], f"{label}.note")


def _validate(doc: dict) -> None:
    require_keys(doc, set(TEMPLATE_KEYS), "preferences")
    require_str_or_none(doc["op_ref"], "op_ref")
    require_str_or_none(doc["home_airport"], "home_airport")
    require_str_list(doc["origin_airports"], "origin_airports")
    require_str_list(doc["avoid_transit"], "avoid_transit")
    require_str_list(doc["avoid_destinations"], "avoid_destinations")
    if not isinstance(doc["departure_days"], list):
        raise UsageError("departure_days must be a list")
    for day in doc["departure_days"]:
        if day not in DAY_TOKENS:
            raise UsageError(f"invalid departure day: {day!r}")
    if not isinstance(doc["avoid_airlines"], list):
        raise UsageError("avoid_airlines must be a list")
    for row in doc["avoid_airlines"]:
        row = require_keys(row, {"code", "name", "strength"}, "avoid_airlines row")
        if row["strength"] not in AIRLINE_STRENGTHS:
            raise UsageError(f"avoid_airlines strength must be one of {sorted(AIRLINE_STRENGTHS)}")
        require_str(row["code"], "avoid_airlines.code")
        require_str(row["name"], "avoid_airlines.name")
    lay = doc["layovers"]
    require_keys(lay, set(LAYOVER_KEYS), "layovers")
    if lay["style"] not in LAYOVER_STYLES:
        raise UsageError(f"layovers.style must be one of {sorted(LAYOVER_STYLES)}")
    require_int(lay["min_connection_minutes"], "layovers.min_connection_minutes")
    require_str_list(lay["prefer_cities"], "layovers.prefer_cities")
    require_str_list(lay["avoid_cities"], "layovers.avoid_cities")
    if not isinstance(doc["statuses"], dict):
        raise UsageError("statuses must be an object")
    for program, tier in doc["statuses"].items():
        require_str(tier, f"statuses[{program}]")
    if not isinstance(doc["status_goals"], list):
        raise UsageError("status_goals must be a list")
    for row in doc["status_goals"]:
        require_keys(row, {"program", "target", "by"}, "status_goals row")
    require_keys(doc["balances"], {"programs", "transferable"}, "balances")
    if not isinstance(doc["credits"], list):
        raise UsageError("credits must be a list")
    for row in doc["credits"]:
        _validate_credit(row, "credits row")
    require_keys(doc["documents"], {"passports", "residency", "visas"}, "documents")
    for section in ("passports", "residency", "visas"):
        if not isinstance(doc["documents"][section], list):
            raise UsageError(f"documents.{section} must be a list")


def init() -> dict:
    def _mut(current: dict) -> dict:
        if current:
            raise StateConflictError("preferences already initialized")
        return _template()

    return atomic_update(prefs_path(), _mut)


def show() -> dict:
    return _load()


def configured() -> bool:
    path = prefs_path()
    if not path.exists():
        return False
    balances = json.loads(path.read_text())["balances"]
    return bool(balances["programs"] or balances["transferable"])


def set_patch(patch: dict) -> dict:
    def _mut(current: dict) -> dict:
        _require_initialized(current)
        unknown = set(patch) - TEMPLATE_KEYS
        if unknown:
            raise UsageError(f"unknown preference keys: {sorted(unknown)}")
        merged = {**current, **patch}
        _validate(merged)
        return merged

    return atomic_update(prefs_path(), _mut)


def set_balance(slug: str, amount: int) -> dict:
    if slug in _load_data("programs.json"):
        bucket = "programs"
    elif slug in _load_data("banks.json"):
        bucket = "transferable"
    else:
        raise UsageError(f"unknown balance slug: {slug!r}")

    def _mut(current: dict) -> dict:
        _require_initialized(current)
        current["balances"][bucket][slug] = amount
        return current

    return atomic_update(prefs_path(), _mut)


def set_status(program: str, tier: str) -> dict:
    if program not in _load_data("programs.json"):
        raise UsageError(f"unknown program: {program!r}")

    def _mut(current: dict) -> dict:
        _require_initialized(current)
        current["statuses"][program] = tier
        return current

    return atomic_update(prefs_path(), _mut)


def credit_add(
    kind: str,
    issuer: str,
    amount: float,
    currency: str,
    expires: str,
    note: str | None = None,
) -> dict:
    row = {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "issuer": issuer,
        "amount": amount,
        "currency": currency,
        "expires": expires,
    }
    if note is not None:
        row["note"] = note

    def _mut(current: dict) -> dict:
        _require_initialized(current)
        _validate_credit(row, "credit")
        current["credits"].append(row)
        return current

    atomic_update(prefs_path(), _mut)
    return row


def credit_list(
    expiring_within: str | None = None, now: Callable[[], datetime] = utcnow
) -> list[dict]:
    credits = _load()["credits"]
    if expiring_within is None:
        return credits
    match = _EXPIRING_RE.match(expiring_within)
    if match is None:
        raise UsageError(f"expiring-within must look like '90d': {expiring_within!r}")
    today = now().date()
    cutoff = today + timedelta(days=int(match.group(1)))
    return [c for c in credits if today <= date.fromisoformat(c["expires"]) <= cutoff]


def credit_remove(credit_id: str) -> dict:
    def _mut(current: dict) -> dict:
        _require_initialized(current)
        remaining = [c for c in current["credits"] if c["id"] != credit_id]
        if len(remaining) == len(current["credits"]):
            raise UsageError(f"no credit with id {credit_id!r}")
        current["credits"] = remaining
        return current

    return atomic_update(prefs_path(), _mut)


prefs_group = click.Group("prefs", help="Durable travel preferences.")


@prefs_group.command("init")
@map_errors
def _init_cmd() -> None:
    emit(init())


@prefs_group.command("show")
@map_errors
def _show_cmd() -> None:
    emit(show())


@prefs_group.command("status")
@map_errors
def _status_cmd() -> None:
    ok = configured()
    emit({"configured": ok})
    if not ok:
        raise NegativePredicate("preferences not configured")


@prefs_group.command("set")
@map_errors
def _set_cmd() -> None:
    patch = json.loads(click.get_text_stream("stdin").read())
    emit(set_patch(patch))


@prefs_group.command("set-balance")
@click.argument("slug")
@click.argument("amount", type=int)
@map_errors
def _set_balance_cmd(slug: str, amount: int) -> None:
    emit(set_balance(slug, amount))


@prefs_group.command("set-status")
@click.argument("program")
@click.argument("tier")
@map_errors
def _set_status_cmd(program: str, tier: str) -> None:
    emit(set_status(program, tier))


@prefs_group.command("credit-add")
@click.option("--kind", required=True)
@click.option("--issuer", required=True)
@click.option("--amount", required=True, type=float)
@click.option("--currency", required=True)
@click.option("--expires", required=True)
@click.option("--note", default=None)
@map_errors
def _credit_add_cmd(
    kind: str, issuer: str, amount: float, currency: str, expires: str, note: str | None
) -> None:
    emit(credit_add(kind, issuer, amount, currency, expires, note))


@prefs_group.command("credit-list")
@click.option("--expiring-within", default=None)
@map_errors
def _credit_list_cmd(expiring_within: str | None) -> None:
    emit(credit_list(expiring_within))


@prefs_group.command("credit-remove")
@click.argument("credit_id")
@map_errors
def _credit_remove_cmd(credit_id: str) -> None:
    emit(credit_remove(credit_id))
