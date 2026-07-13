import json

import pytest
from click.testing import CliRunner

from getaway import registry

FACTOR_IDS = [
    "affordability",
    "airline_preference",
    "departure_days",
    "seat_quality",
    "layovers",
    "transit_risk",
    "return_viability",
    "destination_context",
    "cash_anomaly",
    "status_earning",
    "points_purchase",
    "trip_credits",
]

WST_AIRPORTS = [
    "SFO",
    "SJC",
    "SAN",
    "PDX",
    "DEN",
    "YVR",
    "LAS",
    "SLC",
    "LAX",
    "SEA",
    "PHX",
]


def test_program_count_is_twenty_eight() -> None:
    assert len(registry.programs()) == 28
    assert len(registry.program_slugs()) == 28


def test_four_banks_each_carry_a_currency() -> None:
    banks = registry.banks()
    assert set(banks) == {"amex", "chase", "citi", "capitalone"}
    assert {slug: row["currency"] for slug, row in banks.items()} == {
        "amex": "Membership Rewards",
        "chase": "Ultimate Rewards",
        "citi": "ThankYou Points",
        "capitalone": "Capital One miles",
    }


def test_factor_ids_exactly_as_shipped() -> None:
    assert registry.factor_ids() == FACTOR_IDS


def test_every_transfer_partner_program_resolves() -> None:
    for paths in registry.transfer_partners().values():
        for entry in paths:
            assert registry.is_program(entry["program"]), entry["program"]


def test_every_seat_quality_row_has_product_key() -> None:
    rows = registry.seat_quality()
    assert rows
    assert all("product" in row for row in rows)


def test_expand_region_returns_merged_floor() -> None:
    assert registry.expand_region("WST") == WST_AIRPORTS


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        pytest.param(["SFO", "JFK"], {"SFO", "JFK"}, id="literals"),
        pytest.param(["WST"], {"WST", *WST_AIRPORTS}, id="region"),
        pytest.param(
            ["WST", "JFK"], {"WST", "JFK", *WST_AIRPORTS}, id="literal-beside-region"
        ),
        pytest.param(
            ["SEA"],
            {"SEA", "SIN", "KUL", "BKK", "SGN", "HAN", "MNL", "CGK", "DPS"},
            id="iata-collision-keeps-literal",
        ),
    ],
)
def test_expand_origins(tokens: list[str], expected: set[str]) -> None:
    assert registry.expand_origins(tokens) == expected


def test_expand_origins_null_airports_region_raises() -> None:
    with pytest.raises(registry.NoData):
        registry.expand_origins(["SFO", "QAF"])


def test_expand_region_without_airports_raises() -> None:
    assert registry.region("QAF")["airports"] is None
    with pytest.raises(registry.NoData):
        registry.expand_region("QAF")


def test_region_unknown_code_raises() -> None:
    with pytest.raises(registry.NoData):
        registry.region("ZZZ")


def test_spirit_note_records_shutdown() -> None:
    assert "2026-05-02" in registry.programs()["spirit"]["note"]


def test_cli_continents_lists_six() -> None:
    result = CliRunner().invoke(registry.registry_group, ["continents"])
    assert result.exit_code == 0
    assert json.loads(result.output) == [
        "North America",
        "South America",
        "Africa",
        "Asia",
        "Europe",
        "Oceania",
    ]


def test_cli_programs_domains_projection() -> None:
    result = CliRunner().invoke(registry.registry_group, ["programs", "--domains"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["aeroplan"] == ["aircanada.ca"]
    assert len(payload) == 28


def test_cli_points_pricing_unknown_program_exits_no_data() -> None:
    result = CliRunner().invoke(
        registry.registry_group, ["points-pricing", "--program", "bogus"]
    )
    assert result.exit_code == registry.ExitNoData.exit_code == 4
