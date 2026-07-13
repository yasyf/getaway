import datetime as dt
import fcntl
import json
import os
import sqlite3
import types
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from getaway import paths, prefs, seats, store
from getaway.constants import EXIT_AUTH, EXIT_NEGATIVE, EXIT_NO_DATA, EXIT_OK
from getaway.seats import AuthError, SeatsClient
from getaway.store import NoData, QuotaFloorError

FIXTURES = Path(__file__).parent / "fixtures"
FROZEN = dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
SEARCH_URL = f"{seats.BASE_URL}/search"
AVAIL_URL = f"{seats.BASE_URL}/availability"
ROUTES_URL = f"{seats.BASE_URL}/routes"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def tmp_store(tmp_path: Path) -> store.Store:
    return store.connect(tmp_path / "cache.db", now=lambda: FROZEN)


@pytest.fixture
def client(tmp_store: store.Store) -> SeatsClient:
    return SeatsClient(tmp_store, api_key="test-key")


@respx.mock
def test_search_walks_cursor_dedupes_and_stops(client: SeatsClient) -> None:
    pages = load("search_page.json")
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=pages["page1"], headers={seats.RATE_LIMIT_HEADER: "998"}),
            httpx.Response(200, json=pages["page2"], headers={seats.RATE_LIMIT_HEADER: "997"}),
        ]
    )
    rows = client.search(["SFO"], ["NRT", "HND"], pages=5)
    assert [row["ID"] for row in rows] == ["AAA", "BBB", "CCC"]
    assert respx.get(SEARCH_URL).call_count == 2


@respx.mock
def test_search_second_request_carries_cursor_and_skip(client: SeatsClient) -> None:
    pages = load("search_page.json")
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=pages["page1"]),
            httpx.Response(200, json=pages["page2"]),
        ]
    )
    client.search(["SFO"], ["NRT"], pages=2)
    second = route.calls[1].request.url
    assert second.params["cursor"] == "1720000000"
    assert second.params["skip"] == "2"


@respx.mock
def test_search_respects_page_budget(client: SeatsClient) -> None:
    pages = load("search_page.json")
    respx.get(SEARCH_URL).mock(side_effect=[httpx.Response(200, json=pages["page1"])])
    rows = client.search(["SFO"], ["NRT"], pages=1)
    assert [row["ID"] for row in rows] == ["AAA", "BBB"]
    assert respx.get(SEARCH_URL).call_count == 1


def test_cabin_rows_normalizes_mileage_string_to_int() -> None:
    row = load("search_page.json")["page1"]["data"][0]
    projected = seats.cabin_rows(row)
    assert [entry["cabin"] for entry in projected] == ["Y", "W", "J", "F"]
    business = next(entry for entry in projected if entry["cabin"] == "J")
    assert business == {
        "cabin": "J",
        "available": True,
        "mileage_cost": 80000,
        "remaining_seats": 2,
        "airlines": "NH, UA",
        "direct": False,
    }
    economy = next(entry for entry in projected if entry["cabin"] == "Y")
    assert economy["mileage_cost"] == 35000


def _trip_payload(*trips: dict) -> dict:
    return {"data": list(trips), "booking_links": [{"label": "x", "link": "u", "primary": True}]}


def _seg(cabin: str, order: int) -> dict:
    return {
        "FlightNumber": "AA715",
        "OriginAirport": "SFO",
        "DestinationAirport": "JFK",
        "AircraftName": "Airbus A321",
        "AircraftCode": "321",
        "Cabin": cabin,
        "FareClass": "I",
        "DepartsAt": "2026-09-01T07:00:00Z",
        "ArrivesAt": "2026-09-01T15:00:00Z",
        "Duration": 300,
        "Distance": 2296,
        "Order": order,
    }


def _trip(mileage: int, *cabins: str) -> dict:
    return {
        "MileageCost": mileage,
        "TotalTaxes": 100,
        "TaxesCurrency": "USD",
        "RemainingSeats": 2,
        "TotalDuration": 300,
        "AvailabilitySegments": [_seg(cabin, i) for i, cabin in enumerate(cabins)],
    }


@respx.mock
def test_trip_detail_selects_lowest_mileage_in_cabin(client: SeatsClient) -> None:
    # data[0] is a cheaper economy itinerary; requesting business must skip it
    # and pick the lowest-mileage business itinerary, not the first row.
    payload = _trip_payload(
        _trip(20000, "economy"),
        _trip(90000, "business"),
        _trip(44000, "business"),
    )
    respx.get(f"{seats.BASE_URL}/trips/AAA").mock(return_value=httpx.Response(200, json=payload))
    detail = client.trip_detail("AAA", "J")
    assert detail["mileage"] == 44000


@respx.mock
def test_trip_detail_no_matching_cabin_raises_no_data(client: SeatsClient) -> None:
    payload = _trip_payload(_trip(20000, "economy"), _trip(30000, "economy"))
    respx.get(f"{seats.BASE_URL}/trips/AAA").mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(NoData):
        client.trip_detail("AAA", "J")


@respx.mock
def test_trip_detail_empty_data_raises_no_data(client: SeatsClient) -> None:
    respx.get(f"{seats.BASE_URL}/trips/AAA").mock(
        return_value=httpx.Response(200, json={"data": [], "booking_links": []})
    )
    with pytest.raises(NoData):
        client.trip_detail("AAA", "J")


@respx.mock
def test_trip_detail_normalizes_itinerary(client: SeatsClient) -> None:
    respx.get(f"{seats.BASE_URL}/trips/AAA").mock(
        return_value=httpx.Response(200, json=load("trip_detail.json"))
    )
    detail = client.trip_detail("AAA", "J")
    assert detail["mileage"] == 44000
    assert detail["total_duration"] == 490
    assert [seg["origin"] for seg in detail["segments"]] == ["SFO", "CLT"]
    first = detail["segments"][0]
    assert first["departs_local"] == "2026-09-01T07:00:00"
    assert "Z" not in first["departs_local"]
    assert "Z" not in first["arrives_local"]
    assert first["carrier"] == "AA"
    assert first["flight_number"] == "AA715"
    assert first["aircraft"] == "Airbus A321"
    assert first["cabin"] == "J"
    assert first["duration_minutes"] == 300
    assert detail["layovers"] == [90]
    assert detail["booking_links"][0]["primary"] is True
    assert detail["raw"]["carriers"]["AA"] == "American Airlines"


@respx.mock
def test_availability_projects_cabins_and_records_quota(
    client: SeatsClient, tmp_store: store.Store
) -> None:
    respx.get(AVAIL_URL).mock(
        return_value=httpx.Response(
            200, json=load("availability_page.json"), headers={seats.RATE_LIMIT_HEADER: "500"}
        )
    )
    rows = client.availability("aeroplan")
    assert [row["ID"] for row in rows] == ["DDD", "EEE"]
    assert tmp_store.latest_quota() == {
        "endpoint": "/availability",
        "remaining": 500,
        "recorded_at": FROZEN.isoformat(),
        "reset": False,
    }


@respx.mock
def test_routes_returns_bare_array(client: SeatsClient) -> None:
    respx.get(ROUTES_URL).mock(return_value=httpx.Response(200, json=load("routes.json")))
    rows = client.routes("aeroplan")
    assert [row["OriginAirport"] for row in rows] == ["CAI", "YYZ", "YVR"]


@respx.mock
def test_quota_recorded_once_per_http_call(client: SeatsClient, tmp_store: store.Store) -> None:
    pages = load("search_page.json")
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=pages["page1"], headers={seats.RATE_LIMIT_HEADER: "998"}),
            httpx.Response(200, json=pages["page2"], headers={seats.RATE_LIMIT_HEADER: "997"}),
        ]
    )
    client.search(["SFO"], ["NRT"], pages=2)
    events = tmp_store.quota_events()
    assert [event["remaining"] for event in events] == [997, 998]
    assert tmp_store.latest_quota()["remaining"] == 997


@respx.mock
def test_absent_rate_limit_header_records_nothing(
    client: SeatsClient, tmp_store: store.Store
) -> None:
    respx.get(ROUTES_URL).mock(return_value=httpx.Response(200, json=load("routes.json")))
    client.routes("aeroplan")
    assert tmp_store.quota_events() == []


@respx.mock
def test_non_2xx_raises(client: SeatsClient) -> None:
    respx.get(ROUTES_URL).mock(return_value=httpx.Response(503, json={"error": "unavailable"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.routes("aeroplan")


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
def test_auth_status_raises_auth_error_without_key(client: SeatsClient, status: int) -> None:
    respx.get(ROUTES_URL).mock(return_value=httpx.Response(status, json={"error": "denied"}))
    with pytest.raises(AuthError) as excinfo:
        client.routes("aeroplan")
    assert "test-key" not in str(excinfo.value)


def _write_prefs_op_ref(op_ref: str) -> None:
    # A real v2 prefs doc, not a bare {"op_ref": ...} stub: the op_ref read now
    # routes through the loader, which rejects any pre-v2 (legacy) shape.
    prefs.init()
    prefs.set_patch({"op_ref": op_ref})


def test_env_key_takes_precedence_over_op_ref(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "env-secret-key")
    paths.prefs_path().parent.mkdir(parents=True, exist_ok=True)
    paths.prefs_path().write_text(json.dumps({"op_ref": "op://Vault/seats/credential"}))

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("op should not run when the env key is present")

    monkeypatch.setattr(seats.subprocess, "run", _forbidden)
    assert seats.resolve_api_key() == "env-secret-key"


def test_env_key_resolves_without_a_prefs_file(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "env-secret-key")
    assert not paths.prefs_path().exists()  # onboarding skipped: no prefs on disk
    assert seats.resolve_api_key() == "env-secret-key"  # op_ref read tolerates absence


def test_onboarded_without_op_ref_and_no_env_raises_auth_error(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    prefs.init()
    # A v2 doc where op_ref is unset: the key may be absent entirely, so the read
    # must .get() it (None -> friendly AuthError), never subscript it (KeyError).
    doc = json.loads(paths.prefs_path().read_text())
    del doc["op_ref"]
    paths.prefs_path().write_text(json.dumps(doc))
    with pytest.raises(AuthError):
        seats.resolve_api_key()


def test_op_ref_resolves_via_op_read(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    _write_prefs_op_ref("op://Vault/seats/credential")
    captured: dict[str, list[str]] = {}

    def _fake_run(argv: list[str], **_kwargs: object) -> object:
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="pro_resolved_secret\n", stderr="")

    monkeypatch.setattr(seats.subprocess, "run", _fake_run)
    assert seats.resolve_api_key() == "pro_resolved_secret"
    assert captured["argv"] == ["op", "read", "op://Vault/seats/credential"]


def test_op_ref_prefix_is_enforced(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    _write_prefs_op_ref("Vault/seats/credential")

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("op must not run for a non-op:// reference")

    monkeypatch.setattr(seats.subprocess, "run", _forbidden)
    with pytest.raises(AuthError) as excinfo:
        seats.resolve_api_key()
    assert "Vault/seats/credential" not in str(excinfo.value)


def test_missing_credentials_raise_auth_error(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    with pytest.raises(AuthError):
        seats.resolve_api_key()


def test_legacy_prefs_doc_rejected_when_reading_op_ref(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    paths.prefs_path().parent.mkdir(parents=True, exist_ok=True)
    # Pre-v2 shape: carries the removed `credits` key, lacks travel_instruments.
    paths.prefs_path().write_text(
        json.dumps({"op_ref": "op://Vault/seats/credential", "credits": []})
    )

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("op must not run for a legacy prefs doc")

    monkeypatch.setattr(seats.subprocess, "run", _forbidden)
    with pytest.raises(paths.StateConflictError):
        seats.resolve_api_key()


def test_op_failure_does_not_leak_reference(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    _write_prefs_op_ref("op://Vault/seats/credential")

    def _fail(*_args: object, **_kwargs: object) -> object:
        return types.SimpleNamespace(
            returncode=1, stdout="", stderr="op://Vault/seats/credential not found"
        )

    monkeypatch.setattr(seats.subprocess, "run", _fail)
    with pytest.raises(AuthError) as excinfo:
        seats.resolve_api_key()
    assert "op://Vault/seats/credential" not in str(excinfo.value)


@pytest.mark.parametrize("source", ["env", "op"])
def test_malformed_key_rejected_without_leaking(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    bad_key = "pro_valid\r\nX-Bad: yes"
    if source == "env":
        monkeypatch.setenv(seats.API_KEY_ENV, bad_key)
    else:
        monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
        _write_prefs_op_ref("op://Vault/seats/credential")
        monkeypatch.setattr(
            seats.subprocess,
            "run",
            lambda *_a, **_k: types.SimpleNamespace(returncode=0, stdout=bad_key + "\n", stderr=""),
        )
    with pytest.raises(AuthError) as excinfo:
        seats.resolve_api_key()
    message = str(excinfo.value)
    assert bad_key not in message
    assert "X-Bad" not in message


@respx.mock
def test_search_command_ingests_and_reports(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json=load("search_page.json")["page1"], headers={seats.RATE_LIMIT_HEADER: "900"}
        )
    )
    result = CliRunner().invoke(seats.search_cmd, ["--origin", "SFO", "--dest", "NRT"])
    assert result.exit_code == EXIT_OK
    payload = json.loads(result.output)
    assert payload == {"rows": 2, "new": 2, "quota_remaining": 900}
    assert store.connect(paths.cache_db()).stats()["availability"] == 2


@respx.mock
def test_expand_command_serves_from_cache_on_repeat(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    route = respx.get(f"{seats.BASE_URL}/trips/AAA").mock(
        return_value=httpx.Response(200, json=load("trip_detail.json"))
    )
    runner = CliRunner()
    first = runner.invoke(seats.expand_cmd, ["AAA", "--cabin", "J"])
    assert first.exit_code == EXIT_OK
    assert json.loads(first.output)["mileage"] == 44000
    second = runner.invoke(seats.expand_cmd, ["AAA", "--cabin", "J", "--fresh-within", "6h"])
    assert second.exit_code == EXIT_OK
    assert json.loads(second.output)["mileage"] == 44000
    assert route.call_count == 1


@respx.mock
def test_expand_command_no_matching_cabin_exits_no_data(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    respx.get(f"{seats.BASE_URL}/trips/AAA").mock(
        return_value=httpx.Response(200, json=load("trip_detail.json"))
    )
    result = CliRunner().invoke(seats.expand_cmd, ["AAA", "--cabin", "Y"])
    assert result.exit_code == EXIT_NO_DATA


def test_expand_command_requires_cabin(getaway_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    result = CliRunner().invoke(seats.expand_cmd, ["AAA"])
    assert result.exit_code != EXIT_OK


@respx.mock
def test_search_command_auth_status_exits_auth(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(401, json={"error": "denied"}))
    result = CliRunner().invoke(seats.search_cmd, ["--origin", "SFO", "--dest", "NRT"])
    assert result.exit_code == EXIT_AUTH


def test_search_command_without_credentials_exits_auth(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    result = CliRunner().invoke(seats.search_cmd, ["--origin", "SFO", "--dest", "NRT"])
    assert result.exit_code == EXIT_AUTH


def _reservations(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM quota_reservations").fetchone()[0]
    finally:
        conn.close()


@respx.mock
def test_search_normalizes_string_mileage_to_int(client: SeatsClient) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load("search_page.json")["page1"])
    )
    rows = client.search(["SFO"], ["NRT"])
    row = next(r for r in rows if r["ID"] == "AAA")
    assert row["YMileageCost"] == 35000
    assert isinstance(row["YMileageCost"], int)
    assert row["JMileageCost"] == 80000


def test_client_sets_an_explicit_bounded_timeout(tmp_store: store.Store) -> None:
    timeout = SeatsClient(tmp_store, api_key="test-key")._client.timeout
    assert timeout == seats.HTTP_TIMEOUT
    # bounded on every phase, never None (unbounded), so a hung request cannot
    # outlive its quota reservation.
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (30.0, 30.0, 30.0, 30.0)


@respx.mock
def test_store_lock_is_free_during_the_http_request(client: SeatsClient, tmp_path: Path) -> None:
    lock_path = str(tmp_path / "cache.db.lock")
    observed: list[str] = []

    def during_request(_request: httpx.Request) -> httpx.Response:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # A non-blocking acquire raises if the reserve section still holds the
            # lock; success proves the lock was released before the network call.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed.append("free")
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return httpx.Response(
            200, json=load("routes.json"), headers={seats.RATE_LIMIT_HEADER: "500"}
        )

    respx.get(ROUTES_URL).mock(side_effect=during_request)
    client.routes("aeroplan")
    assert observed == ["free"]


@respx.mock
def test_client_refuses_below_floor_before_any_http(tmp_store: store.Store) -> None:
    tmp_store.record_quota("/search", 50)
    client = SeatsClient(tmp_store, api_key="test-key", floor=100)
    route = respx.get(ROUTES_URL).mock(return_value=httpx.Response(200, json=load("routes.json")))
    with pytest.raises(QuotaFloorError):
        client.routes("aeroplan")
    assert route.call_count == 0  # reservation is refused before the network call


@respx.mock
def test_reservation_released_after_http_error(client: SeatsClient, tmp_path: Path) -> None:
    respx.get(ROUTES_URL).mock(return_value=httpx.Response(503, json={"error": "unavailable"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.routes("aeroplan")
    assert _reservations(tmp_path / "cache.db") == 0


@respx.mock
def test_reservation_released_after_transport_error(client: SeatsClient, tmp_path: Path) -> None:
    respx.get(ROUTES_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(httpx.ConnectError):
        client.routes("aeroplan")
    assert _reservations(tmp_path / "cache.db") == 0


@respx.mock
def test_bootstrap_call_proceeds_despite_floor_and_learns(tmp_store: store.Store) -> None:
    client = SeatsClient(tmp_store, api_key="test-key", floor=999)
    respx.get(ROUTES_URL).mock(
        return_value=httpx.Response(
            200, json=load("routes.json"), headers={seats.RATE_LIMIT_HEADER: "500"}
        )
    )
    client.routes("aeroplan")  # unknown quota bootstraps regardless of the floor
    assert tmp_store.latest_quota()["remaining"] == 500


@respx.mock
def test_search_command_quota_floor_zero_spends_below_default(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    store.connect(paths.cache_db()).record_quota("/search", 50)  # below the default floor of 100
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json=load("search_page.json")["page1"], headers={seats.RATE_LIMIT_HEADER: "49"}
        )
    )
    result = CliRunner().invoke(
        seats.search_cmd, ["--origin", "SFO", "--dest", "NRT", "--quota-floor", "0"]
    )
    assert result.exit_code == EXIT_OK


@respx.mock
def test_search_command_default_floor_blocks_spend_below_floor(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(seats.API_KEY_ENV, "pro_test")
    store.connect(paths.cache_db()).record_quota("/search", 50)
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load("search_page.json")["page1"])
    )
    result = CliRunner().invoke(seats.search_cmd, ["--origin", "SFO", "--dest", "NRT"])
    assert result.exit_code == EXIT_NEGATIVE
    assert route.call_count == 0
