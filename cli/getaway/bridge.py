"""Cash-leg pricing for hybrid positioning hops (``getaway bridge <slug>``).

Prices each onward cash hop (gateway -> onward_dest, on the gateway-arrival date) through fli's
library API (Google Flights). Three hardenings from recorded papercuts are codified here:

* **OKA Airport-enum alias.** ``fli.models.Airport`` keys members by IATA code but values them by
  airport name; OKA (Naha, Okinawa) shares the value "Naha Airport" with NAH (Tahuna, Indonesia),
  so ``Airport['OKA']`` is a silent alias of ``Airport.NAH``. Every OKA query then goes to Google
  as NAH (zero flights, no error) and every response row citing "OKA" fails to decode. The fix
  patches both paths: the encoded request rewrites the aliased ``NAH`` token back to ``OKA``, and
  the decode cache learns ``OKA`` -> the shared member.
* **Origin-local "today".** A JST departure date can already be past while it is still evening in
  the Pacific; Google then returns zero itineraries silently. A pair whose date is past in the
  gateway's local day is a non-retryable ``failed`` state, never a priced quote.
* **Zero is a failure, not "no fare".** Zero results for a viable route surface as
  ``failed{retryability}`` — never as a "no cash fare" quote we can't actually stand behind.

Spends no seats.aero quota; cabin choice per cash leg is model judgment fed by duration fit facts,
so bridge quotes the positioning cabin (economy) and reports duration.
"""

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import click

from getaway import prefs, serp, trips
from getaway.paths import emit, map_errors, utcnow

Row = dict[str, Any]

# The OKA alias serializes to the shared member name "NAH"; the fix rewrites it back in ``format``'s
# request structure (encoding-independent), where an airport code is the only bare "NAH" string.
_ALIAS_FROM = "NAH"
_ALIAS_TO = "OKA"
_TOP_N = 5

# Origin-local day offsets for the positioning hubs where the JST-ahead trap actually bites; an
# unknown gateway skips the past-date guard and relies on the zero-results -> failed surface.
_UTC_OFFSET_HOURS = {
    "HND": 9,
    "NRT": 9,
    "KIX": 9,
    "ITM": 9,
    "OKA": 9,
    "FUK": 9,
    "CTS": 9,
    "ICN": 9,
    "GMP": 9,
    "PVG": 8,
    "PEK": 8,
    "HKG": 8,
    "TPE": 8,
    "SIN": 8,
    "BKK": 7,
}

_oka_installed = False


@dataclass(frozen=True)
class _SearchOutcome:
    results: list | None
    source: str
    failure_detail: str | None = None


def _rewrite_alias(obj: Any) -> Any:
    # A bare "NAH" string in the request structure is only ever the OKA-aliased airport code.
    if obj == _ALIAS_FROM:
        return _ALIAS_TO
    if isinstance(obj, list):
        return [_rewrite_alias(item) for item in obj]
    return obj


def _install_oka_fix() -> None:
    global _oka_installed
    if _oka_installed:
        return
    from fli.models.airport import Airport
    from fli.models.google_flights import flights as flights_model
    from fli.search import _decoders

    _decoders._AIRPORT_BY_CODE.setdefault("OKA", Airport["NAH"])
    original_format = flights_model.FlightSearchFilters.format

    def format(self: Any) -> list:  # rewrite the aliased NAH code so Google receives OKA
        return _rewrite_alias(original_format(self))

    flights_model.FlightSearchFilters.format = format
    _oka_installed = True


def _origin_local_today(gateway: str, now: dt.datetime) -> dt.date | None:
    offset = _UTC_OFFSET_HOURS.get(gateway)
    if offset is None:
        return None
    return (now.astimezone(dt.timezone.utc) + dt.timedelta(hours=offset)).date()


def _search_flights(origin: str, dest: str, date: str) -> list | None:
    _install_oka_fix()
    from fli.models import (
        Airport,
        FlightSearchFilters,
        FlightSegment,
        MaxStops,
        PassengerInfo,
        SeatType,
        SortBy,
        TripType,
    )
    from fli.search import SearchFlights

    segment = FlightSegment(
        departure_airport=[[Airport[origin], 0]],
        arrival_airport=[[Airport[dest], 0]],
        travel_date=date,
    )
    filters = FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[segment],
        seat_type=SeatType.ECONOMY,
        stops=MaxStops.ANY,
        sort_by=SortBy.CHEAPEST,
    )
    return SearchFlights().search(filters, top_n=_TOP_N)


def _priced(results: list | None) -> list:
    return [result for result in (results or []) if result.price is not None]


def _search_with_fallback(origin: str, dest: str, date: str) -> _SearchOutcome:
    try:
        results = _search_flights(origin, dest, date)
    except Exception as err:
        fli_error = err
    else:
        if _priced(results):
            return _SearchOutcome(results, "fli")
        fli_error = None

    api_key = serp.resolve_api_key_if_available()
    if api_key is None:
        reason = f"search error: {fli_error}" if fli_error is not None else "no results returned"
        return _SearchOutcome(None, "fli", f"{reason}; fallback: no serpapi key")
    return _SearchOutcome(
        serp.search(origin, dest, date, "economy", api_key=api_key),
        "serpapi",
    )


def _local(value: dt.datetime) -> str:
    # Google Flights times are the airport's local wall clock — match the seats.aero naive shape.
    return value.replace(tzinfo=None).isoformat(timespec="minutes")


def _iata(airport: Any) -> str:
    # fli enum members give the IATA code via ``.name`` (raw string for serp); ``_rewrite_alias``
    # maps an OKA connection back off the aliased "NAH" member (upstream punitarani/fli#131).
    return _rewrite_alias(airport.name if isinstance(airport, Enum) else airport)


def _quote(gateway: str, dest: str, date: str, result: Any, source: str) -> dict:
    first, last = result.legs[0], result.legs[-1]
    return {
        "gateway": gateway,
        "onward_dest": dest,
        "date": date,
        "cabin": "economy",
        "source": source,
        "price": result.price,
        "currency": result.currency or "USD",
        "duration_minutes": result.duration,
        "stops": result.stops,
        "connections": [_iata(leg.arrival_airport) for leg in result.legs[:-1]],
        "airline": first.airline.name,
        "flight_number": first.flight_number,
        "departs_local": _local(first.departure_datetime),
        "arrives_local": _local(last.arrival_datetime),
    }


def _price_pair(pair: Row, now: Callable[[], dt.datetime], search: Callable) -> dict:
    gateway, dest, date = pair["gateway"], pair["onward_dest"], pair["date"]
    base = {"gateway": gateway, "onward_dest": dest, "date": date}
    local_today = _origin_local_today(gateway, now())
    if local_today is not None and dt.date.fromisoformat(date) < local_today:
        return {
            "state": "failed",
            **base,
            "reason": "departure date past in origin-local time",
            "retryable": False,
        }
    try:
        searched = search(gateway, dest, date)
    except Exception as err:  # Search backends surface HTTP/timeout/parse failures as exceptions.
        return {"state": "failed", **base, "reason": f"search error: {err}", "retryable": True}
    outcome = searched if isinstance(searched, _SearchOutcome) else _SearchOutcome(searched, "fli")
    priced = _priced(outcome.results)
    if not priced:
        # A viable positioning route with zero priced results reads as a failure to verify, never
        # as a confirmed absence of cash fares.
        return {
            "state": "failed",
            **base,
            "reason": outcome.failure_detail or "no results returned",
            "retryable": True,
        }
    return {"state": "quoted", "quote": _quote(gateway, dest, date, priced[0], outcome.source)}


def run(
    slug: str,
    leg: str,
    now: Callable[[], dt.datetime] = utcnow,
    search: Callable = _search_with_fallback,
) -> dict:
    trip = trips.show(slug)
    prefs_doc = prefs.show()
    node_id = f"bridge:{leg}"
    inputs_fp = trips.capture_inputs_fp(trip, prefs_doc, node_id)
    onward = json.loads(trips.artifact_read(slug, f"legs/{leg}/onward.json"))
    quotes: list[dict] = []
    failures: list[dict] = []
    for pair in onward["bridge_pairs"]:
        priced = _price_pair(pair, now, search)
        if priced["state"] == "quoted":
            quotes.append(priced["quote"])
        else:
            failures.append({k: v for k, v in priced.items() if k != "state"})
    doc = {"quotes": quotes, "failures": failures}
    trips.artifact_write(slug, f"legs/{leg}/bridge.json", json.dumps(doc, separators=(",", ":")))
    trips.phase_done(slug, node_id, inputs_fp=inputs_fp, now=now)
    return {"quotes": len(quotes), "failures": len(failures)}


@click.command("bridge")
@click.argument("slug")
@click.option("--leg", required=True)
@map_errors
def bridge_cmd(slug: str, leg: str) -> None:
    emit(run(slug, leg))
