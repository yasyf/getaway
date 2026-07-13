import datetime as dt
import json
import types
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from getaway import paths, seats, store
from getaway.constants import EXIT_AUTH, EXIT_OK
from getaway.seats import AuthError, SeatsClient

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
    respx.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(200, json=pages["page1"])]
    )
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


@respx.mock
def test_trip_detail_normalizes_itinerary(client: SeatsClient) -> None:
    respx.get(f"{seats.BASE_URL}/trips/AAA").mock(
        return_value=httpx.Response(200, json=load("trip_detail.json"))
    )
    detail = client.trip_detail("AAA")
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


def test_op_ref_resolves_via_op_read(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    paths.prefs_path().parent.mkdir(parents=True, exist_ok=True)
    paths.prefs_path().write_text(json.dumps({"op_ref": "op://Vault/seats/credential"}))
    captured: dict[str, list[str]] = {}

    def _fake_run(argv: list[str], **_kwargs: object) -> object:
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="pro_resolved_secret\n", stderr="")

    monkeypatch.setattr(seats.subprocess, "run", _fake_run)
    assert seats.resolve_api_key() == "pro_resolved_secret"
    assert captured["argv"] == ["op", "read", "op://Vault/seats/credential"]


def test_op_ref_prefix_is_enforced(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    paths.prefs_path().parent.mkdir(parents=True, exist_ok=True)
    paths.prefs_path().write_text(json.dumps({"op_ref": "Vault/seats/credential"}))

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


def test_op_failure_does_not_leak_reference(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    paths.prefs_path().parent.mkdir(parents=True, exist_ok=True)
    paths.prefs_path().write_text(json.dumps({"op_ref": "op://Vault/seats/credential"}))

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
        paths.prefs_path().parent.mkdir(parents=True, exist_ok=True)
        paths.prefs_path().write_text(json.dumps({"op_ref": "op://Vault/seats/credential"}))
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
    first = runner.invoke(seats.expand_cmd, ["AAA"])
    assert first.exit_code == EXIT_OK
    assert json.loads(first.output)["mileage"] == 44000
    second = runner.invoke(seats.expand_cmd, ["AAA", "--fresh-within", "6h"])
    assert second.exit_code == EXIT_OK
    assert json.loads(second.output)["mileage"] == 44000
    assert route.call_count == 1


def test_search_command_without_credentials_exits_auth(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(seats.API_KEY_ENV, raising=False)
    result = CliRunner().invoke(seats.search_cmd, ["--origin", "SFO", "--dest", "NRT"])
    assert result.exit_code == EXIT_AUTH
