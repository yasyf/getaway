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
    "destination_context",
    "cash_anomaly",
    "status_earning",
    "points_purchase",
    "trip_credits",
    "window_fit",
    "trip_length_fit",
    "departure_day_fit",
    "mileage_fit",
    "cabin_fit",
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


HOTEL_SLUGS = {"hyatt", "hilton", "marriott", "ihg", "choice", "wyndham"}


def test_program_count_splits_airlines_and_hotels() -> None:
    progs = registry.programs()
    assert len(progs) == 34
    assert len(registry.program_slugs()) == 34
    assert len(registry.programs_by_kind("airline")) == 28
    assert len(registry.programs_by_kind("hotel")) == 6


def test_hotel_rows_carry_capabilities() -> None:
    hotels = registry.programs_by_kind("hotel")
    assert set(hotels) == HOTEL_SLUGS
    for slug, row in hotels.items():
        assert row["kind"] == "hotel"
        assert row["rooms_aero"] is True
        assert row["seats_aero"] is False
        assert row["domains"], slug


def test_every_program_declares_kind_and_valid_gather_auth() -> None:
    for slug, row in registry.programs().items():
        assert row["kind"] in ("airline", "hotel"), slug
        assert row["gather_auth"] in registry.GATHER_AUTH_CLASSES, slug


def test_token_auth_airlines_are_exactly_the_indexeddb_hosts() -> None:
    token = {s for s, r in registry.programs().items() if r["gather_auth"] == "token"}
    assert token == {"delta", "american", "united", "jetblue", "aeroplan", "qatar", "singapore"}


def test_banks_declare_gather_auth() -> None:
    auth = {slug: row["gather_auth"] for slug, row in registry.banks().items()}
    assert auth == {
        "amex": "device_wall",
        "capitalone": "token",
        "chase": "cookie",
        "citi": "cookie",
    }
    for row in registry.banks().values():
        assert row["gather_auth"] in registry.GATHER_AUTH_CLASSES


def test_every_program_and_bank_declares_awardwallet_code() -> None:
    for slug, row in {**registry.programs(), **registry.banks()}.items():
        assert row["awardwallet"] is None or isinstance(row["awardwallet"], str), slug


def test_awardwallet_codes_are_unique_across_programs_and_banks() -> None:
    codes = [
        row["awardwallet"]
        for row in {**registry.programs(), **registry.banks()}.values()
        if row["awardwallet"] is not None
    ]
    assert len(codes) == len(set(codes))


def test_awardwallet_map_resolves_known_codes() -> None:
    mapping = registry.awardwallet_map()
    assert mapping["aeroplan"] == "aeroplan"
    assert mapping["membershiprewards"] == "amex"


def test_sells_points_agrees_with_points_pricing() -> None:
    progs = registry.programs()
    pricing = registry.points_pricing()
    assert set(progs) == set(pricing)  # every program has a pricing row
    for slug in progs:
        assert progs[slug]["sells_points"] == pricing[slug]["sells_points"], slug


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


def test_card_products_cover_every_bank() -> None:
    assert set(registry.card_products()) == set(registry.banks())


def test_every_card_product_has_a_name() -> None:
    for products in registry.card_products().values():
        assert all("name" in row for row in products.values())


def test_every_card_gate_product_resolves() -> None:
    products = registry.card_products()
    for bank, paths in registry.transfer_partners().items():
        for entry in paths:
            for product in entry.get("card_gate", {}).get("products", []):
                assert product in products[bank], f"{bank}:{product}"


def test_card_gates_are_present_only_for_chase_and_citi() -> None:
    partners = registry.transfer_partners()
    assert {
        bank: all("card_gate" in entry for entry in paths) for bank, paths in partners.items()
    } == {
        "amex": False,
        "chase": True,
        "citi": True,
        "capitalone": False,
    }
    assert all("card_gate" not in entry for entry in partners["amex"])
    assert all("card_gate" not in entry for entry in partners["capitalone"])


def test_card_gates_pin_the_qualifying_products() -> None:
    chase_gate = [
        "sapphire-reserve",
        "sapphire-reserve-business",
        "sapphire-preferred",
        "ink-business-preferred",
        "ink-plus",
    ]
    citi_gate = ["strata-premier", "strata-elite", "prestige"]
    partners = registry.transfer_partners()
    gates = {
        bank: [entry["card_gate"]["products"] for entry in paths]
        for bank, paths in partners.items()
        if bank in ("chase", "citi")
    }
    assert gates == {
        "chase": [chase_gate] * len(partners["chase"]),
        "citi": [citi_gate] * len(partners["citi"]),
    }


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
        pytest.param(["WST", "JFK"], {"WST", "JFK", *WST_AIRPORTS}, id="literal-beside-region"),
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


def test_spirit_awardwallet_is_none() -> None:
    assert registry.programs()["spirit"]["awardwallet"] is None


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
    assert len(payload) == 34


def test_cli_programs_kind_hotel_filter() -> None:
    result = CliRunner().invoke(
        registry.registry_group, ["programs", "--kind", "hotel", "--domains"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == HOTEL_SLUGS
    assert payload["hyatt"] == ["world.hyatt.com", "hyatt.com"]


def test_cli_programs_rooms_aero_matches_hotels() -> None:
    result = CliRunner().invoke(registry.registry_group, ["programs", "--rooms-aero"])
    assert result.exit_code == 0
    assert set(json.loads(result.output)) == HOTEL_SLUGS


def test_cli_hosts_unifies_programs_and_banks_with_auth() -> None:
    result = CliRunner().invoke(registry.registry_group, ["hosts"])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert len(rows) == 34 + 4
    by_slug = {r["slug"]: r for r in rows}
    assert by_slug["delta"] == {
        "slug": "delta",
        "kind": "airline",
        "gather_auth": "token",
        "hosts": ["delta.com"],
    }
    assert by_slug["hyatt"] == {
        "slug": "hyatt",
        "kind": "hotel",
        "gather_auth": "cookie",
        "hosts": ["world.hyatt.com", "hyatt.com"],
    }
    assert by_slug["amex"] == {
        "slug": "amex",
        "kind": "bank",
        "gather_auth": "device_wall",
        "hosts": ["americanexpress.com", "global.americanexpress.com"],
    }


def test_cli_hosts_filters_by_kind_and_auth() -> None:
    hotels = CliRunner().invoke(registry.registry_group, ["hosts", "--kind", "hotel"])
    assert {r["slug"] for r in json.loads(hotels.output)} == HOTEL_SLUGS
    device = CliRunner().invoke(registry.registry_group, ["hosts", "--gather-auth", "device_wall"])
    assert [r["slug"] for r in json.loads(device.output)] == ["amex"]


def test_cli_card_products_filters_by_bank() -> None:
    result = CliRunner().invoke(registry.registry_group, ["card-products", "--bank", "chase"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"chase": registry.card_products()["chase"]}


def test_cli_card_products_unknown_bank_exits_no_data() -> None:
    result = CliRunner().invoke(registry.registry_group, ["card-products", "--bank", "bogus"])
    assert result.exit_code == registry.ExitNoData.exit_code == 4


def test_hotel_programs_reachable_by_transfer() -> None:
    tp = registry.transfer_partners()
    reach = {
        h: {b for b, paths in tp.items() if any(e["program"] == h for e in paths)}
        for h in HOTEL_SLUGS
    }
    assert reach == {
        "hyatt": {"chase"},
        "hilton": {"amex"},
        "marriott": {"amex", "chase"},
        "ihg": {"chase"},
        "choice": {"amex", "citi", "capitalone"},
        "wyndham": {"chase", "citi", "capitalone"},
    }


def test_cli_points_pricing_unknown_program_exits_no_data() -> None:
    result = CliRunner().invoke(registry.registry_group, ["points-pricing", "--program", "bogus"])
    assert result.exit_code == registry.ExitNoData.exit_code == 4
