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
DAY_TOKENS = frozenset({"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})
LAYOVER_KEYS = frozenset({"style", "min_connection_minutes", "prefer_cities", "avoid_cities"})

INSTRUMENT_TYPES = frozenset({"monetary_credit", "hotel_night_certificate", "companion_fare"})
CERT_CAP_TYPES = frozenset({"points", "category", "anytime"})
MONETARY_CREDIT_REQUIRED = frozenset({"id", "type", "issuer", "amount", "currency", "expires"})
HOTEL_CERT_REQUIRED = frozenset({"id", "type", "program", "nights", "cap", "expires"})
COMPANION_FARE_REQUIRED = frozenset({"id", "type", "issuer", "expires"})
INSTRUMENT_OPTIONAL = frozenset({"note"})

_EXPIRING_RE = re.compile(r"^(\d+)d$")


def _template() -> dict:
    return {
        "op_ref": None,
        "awardwallet_op_ref": None,
        "serpapi_op_ref": None,
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
        "travel_instruments": [],
        "documents": {"passports": [], "residency": [], "visas": []},
        "cards": [],
    }


TEMPLATE_KEYS = frozenset(_template())


def _load_data(name: str) -> dict:
    return json.loads((resources.files("getaway") / "data" / name).read_text())


def _hotel_programs() -> set[str]:
    return {slug for slug, row in _load_data("programs.json").items() if row["kind"] == "hotel"}


def _card_products() -> dict:
    return _load_data("card_products.json")


def _load() -> dict:
    path = prefs_path()
    if not path.exists():
        raise StateConflictError("preferences not initialized; run prefs init")
    return json.loads(path.read_text())


def load_or_empty() -> dict:
    """Preferences doc, or an empty dict when onboarding is skipped."""
    path = prefs_path()
    return json.loads(path.read_text()) if path.exists() else {}


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


def _require_int_dict(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise UsageError(f"{label} must be an object")
    for key, amount in value.items():
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise UsageError(f"{label}[{key}] must be an integer")


def _check_positive_number(value: object, label: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise UsageError(f"{label} must be a number")
    if value <= 0:
        raise UsageError(f"{label} must be positive")


def _validate_cert_cap(cap: object, label: str) -> None:
    if not isinstance(cap, dict):
        raise UsageError(f"{label} must be an object")
    ctype = cap.get("type")
    if ctype == "points":
        cap = require_keys(cap, {"type", "points"}, label)
        if require_int(cap["points"], f"{label}.points") < 1:
            raise UsageError(f"{label}.points must be at least 1")
    elif ctype == "category":
        cap = require_keys(cap, {"type", "category"}, label)
        require_str(cap["category"], f"{label}.category")
    elif ctype == "anytime":
        require_keys(cap, {"type"}, label)
    else:
        raise UsageError(f"{label}.type must be one of {sorted(CERT_CAP_TYPES)}")


def _validate_monetary_credit(row: dict, label: str) -> None:
    require_keys(row, set(MONETARY_CREDIT_REQUIRED), label, optional=INSTRUMENT_OPTIONAL)
    require_str(row["issuer"], f"{label}.issuer")
    _check_positive_number(row["amount"], f"{label}.amount")
    require_str(row["currency"], f"{label}.currency")
    _check_iso_date(row["expires"], f"{label}.expires")
    if "note" in row:
        require_str(row["note"], f"{label}.note")


def _validate_hotel_night_certificate(row: dict, label: str) -> None:
    require_keys(row, set(HOTEL_CERT_REQUIRED), label, optional=INSTRUMENT_OPTIONAL)
    program = require_str(row["program"], f"{label}.program")
    if program not in _hotel_programs():
        raise UsageError(f"{label}.program must be a hotel program: {program!r}")
    if require_int(row["nights"], f"{label}.nights") < 1:
        raise UsageError(f"{label}.nights must be at least 1")
    _validate_cert_cap(row["cap"], f"{label}.cap")
    _check_iso_date(row["expires"], f"{label}.expires")
    if "note" in row:
        require_str(row["note"], f"{label}.note")


def _validate_companion_fare(row: dict, label: str) -> None:
    require_keys(row, set(COMPANION_FARE_REQUIRED), label, optional=INSTRUMENT_OPTIONAL)
    require_str(row["issuer"], f"{label}.issuer")
    _check_iso_date(row["expires"], f"{label}.expires")
    if "note" in row:
        require_str(row["note"], f"{label}.note")


_INSTRUMENT_VALIDATORS = {
    "monetary_credit": _validate_monetary_credit,
    "hotel_night_certificate": _validate_hotel_night_certificate,
    "companion_fare": _validate_companion_fare,
}


def _validate_instrument(row: object, label: str) -> None:
    if not isinstance(row, dict):
        raise UsageError(f"{label} must be an object")
    validator = _INSTRUMENT_VALIDATORS.get(row.get("type"))
    if validator is None:
        raise UsageError(f"{label}.type must be one of {sorted(INSTRUMENT_TYPES)}")
    validator(row, label)


def _validate(doc: dict) -> None:
    require_keys(doc, set(TEMPLATE_KEYS), "preferences")
    require_str_or_none(doc["op_ref"], "op_ref")
    require_str_or_none(doc["awardwallet_op_ref"], "awardwallet_op_ref")
    require_str_or_none(doc["serpapi_op_ref"], "serpapi_op_ref")
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
    balances = require_keys(doc["balances"], {"programs", "transferable"}, "balances")
    _require_int_dict(balances["programs"], "balances.programs")
    _require_int_dict(balances["transferable"], "balances.transferable")
    if not isinstance(doc["travel_instruments"], list):
        raise UsageError("travel_instruments must be a list")
    for row in doc["travel_instruments"]:
        _validate_instrument(row, "travel_instruments row")
    require_keys(doc["documents"], {"passports", "residency", "visas"}, "documents")
    for section in ("passports", "residency", "visas"):
        require_str_list(doc["documents"][section], f"documents.{section}")
    if not isinstance(doc["cards"], list):
        raise UsageError("cards must be a list")
    banks = _load_data("banks.json")
    products = _card_products()
    seen = set()
    for row in doc["cards"]:
        row = require_keys(row, {"issuer", "product"}, "cards row")
        issuer = require_str(row["issuer"], "cards.issuer")
        product = require_str(row["product"], "cards.product")
        if issuer not in banks:
            raise UsageError(f"cards.issuer must be a bank slug: {issuer!r}")
        if product not in products[issuer]:
            raise UsageError(f"cards.product is not a {issuer} product: {product!r}")
        pair = (issuer, product)
        if pair in seen:
            raise UsageError(f"duplicate card: {issuer}:{product}")
        seen.add(pair)


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
    doc = json.loads(path.read_text())
    balances = doc["balances"]
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


def instrument_add(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise UsageError("instrument must be an object")
    if "id" in spec:
        raise UsageError("instrument id is generated; omit it")
    row = {"id": uuid.uuid4().hex[:8], **spec}

    def _mut(current: dict) -> dict:
        _require_initialized(current)
        _validate_instrument(row, "instrument")
        current["travel_instruments"].append(row)
        return current

    atomic_update(prefs_path(), _mut)
    return row


def instrument_list(
    expiring_within: str | None = None, now: Callable[[], datetime] = utcnow
) -> list[dict]:
    instruments = _load()["travel_instruments"]
    if expiring_within is None:
        return instruments
    match = _EXPIRING_RE.match(expiring_within)
    if match is None:
        raise UsageError(f"expiring-within must look like '90d': {expiring_within!r}")
    today = now().date()
    cutoff = today + timedelta(days=int(match.group(1)))
    return [i for i in instruments if today <= date.fromisoformat(i["expires"]) <= cutoff]


def instrument_remove(instrument_id: str) -> dict:
    def _mut(current: dict) -> dict:
        _require_initialized(current)
        remaining = [i for i in current["travel_instruments"] if i["id"] != instrument_id]
        if len(remaining) == len(current["travel_instruments"]):
            raise UsageError(f"no instrument with id {instrument_id!r}")
        current["travel_instruments"] = remaining
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
    try:
        patch = json.loads(click.get_text_stream("stdin").read())
    except json.JSONDecodeError as err:
        raise UsageError(f"invalid JSON on stdin: {err}") from err
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


@prefs_group.command("instrument-add")
@map_errors
def _instrument_add_cmd() -> None:
    try:
        spec = json.loads(click.get_text_stream("stdin").read())
    except json.JSONDecodeError as err:
        raise UsageError(f"invalid JSON on stdin: {err}") from err
    emit(instrument_add(spec))


@prefs_group.command("instrument-list")
@click.option("--expiring-within", default=None)
@map_errors
def _instrument_list_cmd(expiring_within: str | None) -> None:
    emit(instrument_list(expiring_within))


@prefs_group.command("instrument-remove")
@click.argument("instrument_id")
@map_errors
def _instrument_remove_cmd(instrument_id: str) -> None:
    emit(instrument_remove(instrument_id))
