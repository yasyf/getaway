import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from getaway import afford, prefs


def _prefs(
    programs: dict | None = None,
    transferable: dict | None = None,
    cards: list[dict] | None = None,
) -> dict:
    return {
        "balances": {"programs": programs or {}, "transferable": transferable or {}},
        "cards": cards or [],
    }


def test_covered_outright_has_zero_shortfall_and_no_purchase() -> None:
    result = afford.afford("aeroplan", 80000, _prefs({"aeroplan": 100000}))
    assert result["covered"] is True
    assert result["balance"] == 100000
    assert result["shortfall"] == 0
    assert result["purchase"] is None


def test_shortfall_marks_only_the_covering_transfer_path() -> None:
    result = afford.afford(
        "aeroplan",
        80000,
        _prefs({"aeroplan": 50000}, {"amex": 50000, "chase": 10000}),
    )
    assert result["shortfall"] == 30000
    by_bank = {p["bank"]: p for p in result["transfer_paths"]}
    assert set(by_bank) == {"amex", "chase", "capitalone"}
    assert by_bank["amex"] == {
        "bank": "amex",
        "bank_balance": 50000,
        "ratio": "1:1",
        "points_required": 30000,
        "covers": True,
        "note": None,
        "card_access": None,
    }
    assert by_bank["chase"]["covers"] is False
    assert by_bank["capitalone"]["bank_balance"] == 0


@pytest.mark.parametrize(
    ("bank", "ratio", "expected_required"),
    [
        pytest.param("capitalone", "1:0.6", 16667, id="ratio-1-to-0.6-ceils-up"),
        pytest.param("amex", "1:0.8", 12500, id="ratio-1-to-0.8-exact"),
        pytest.param("citi", "1:1", 10000, id="ratio-1-to-1"),
    ],
)
def test_transfer_ratio_arithmetic(bank: str, ratio: str, expected_required: int) -> None:
    result = afford.afford("jetblue", 10000, _prefs())
    path = next(p for p in result["transfer_paths"] if p["bank"] == bank)
    assert path["ratio"] == ratio
    assert path["points_required"] == expected_required


def test_chase_transfer_rounds_up_to_increment() -> None:
    # Chase moves in 1,000-point increments: 60,500 points cannot cover a
    # 60,500-mile 1:1 shortfall — the transfer must round up to 61,000.
    result = afford.afford("united", 60500, _prefs({}, {"chase": 60500}))
    chase = next(p for p in result["transfer_paths"] if p["bank"] == "chase")
    assert chase["ratio"] == "1:1"
    assert chase["points_required"] == 61000
    assert chase["covers"] is False


def test_chase_increment_already_on_boundary_unchanged() -> None:
    result = afford.afford("united", 60000, _prefs({}, {"chase": 60000}))
    chase = next(p for p in result["transfer_paths"] if p["bank"] == "chase")
    assert chase["points_required"] == 60000
    assert chase["covers"] is True


def test_non_chase_bank_has_no_increment_rounding() -> None:
    # Amex has no transfer increment, so 60,500 required stays exact.
    result = afford.afford("aeroplan", 60500, _prefs({}, {"amex": 60500}))
    amex = next(p for p in result["transfer_paths"] if p["bank"] == "amex")
    assert amex["points_required"] == 60500
    assert amex["covers"] is True


@pytest.mark.parametrize(
    ("cards", "status", "held"),
    [
        pytest.param(
            [{"issuer": "chase", "product": "sapphire-reserve"}],
            "on_file",
            ["sapphire-reserve"],
            id="qualifying-chase-card-on-file",
        ),
        pytest.param(
            [{"issuer": "chase", "product": "freedom-unlimited"}],
            "none_on_file",
            [],
            id="non-qualifying-chase-card-on-file",
        ),
        pytest.param(
            [{"issuer": "amex", "product": "platinum"}],
            "unknown",
            [],
            id="only-other-bank-card-on-file",
        ),
        pytest.param([], "unknown", [], id="no-cards-on-file"),
    ],
)
def test_chase_card_access(cards: list[dict], status: str, held: list[str]) -> None:
    result = afford.afford("hyatt", 60000, _prefs(cards=cards))
    chase = next(p for p in result["transfer_paths"] if p["bank"] == "chase")
    assert chase["card_access"] == {
        "required": [
            "sapphire-reserve",
            "sapphire-reserve-business",
            "sapphire-preferred",
            "ink-business-preferred",
            "ink-plus",
        ],
        "held": held,
        "status": status,
    }


def test_ungated_amex_path_has_no_card_access() -> None:
    result = afford.afford(
        "aeroplan",
        60000,
        _prefs(cards=[{"issuer": "amex", "product": "platinum"}]),
    )
    amex = next(p for p in result["transfer_paths"] if p["bank"] == "amex")
    assert amex["card_access"] is None


def test_include_purchase_costs_from_typical_sale_cents() -> None:
    result = afford.afford("aeroplan", 100000, _prefs(), include_purchase=True)
    assert result["purchase"] == {
        "rate_cents": 1.44,
        "cost_usd": 1440.0,
        "cap_note": None,
    }


def test_include_purchase_falls_back_to_buy_rate() -> None:
    result = afford.afford("aeromexico", 10000, _prefs(), include_purchase=True)
    assert result["purchase"] == {
        "rate_cents": 1.5,
        "cost_usd": 150.0,
        "cap_note": None,
    }


def test_include_purchase_flags_annual_cap_exceeded() -> None:
    result = afford.afford("delta", 100000, _prefs(), include_purchase=True)
    assert result["purchase"]["cap_note"] == "100000 exceeds delta annual purchase cap of 60000"


@pytest.mark.parametrize(
    ("program", "reason"),
    [
        pytest.param("singapore", "singapore does not sell points", id="non-seller"),
        pytest.param("eurobonus", "no public buy rate for eurobonus", id="null-rate-seller"),
    ],
)
def test_null_rate_program_yields_purchase_with_null_cost_and_reason(
    program: str, reason: str
) -> None:
    purchase = afford.afford(program, 50000, _prefs(), include_purchase=True)["purchase"]
    assert purchase["rate_cents"] is None
    assert purchase["cost_usd"] is None
    assert purchase["reason"] == reason


def test_no_include_purchase_leaves_purchase_null_even_with_shortfall() -> None:
    result = afford.afford("aeroplan", 100000, _prefs())
    assert result["shortfall"] == 100000
    assert result["purchase"] is None


def test_cli_afford_loads_prefs_from_home(getaway_home: Path) -> None:
    prefs.init()
    prefs.set_balance("united", 40000)
    result = CliRunner().invoke(afford.afford_cmd, ["--program", "united", "--miles", "90000"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["balance"] == 40000
    assert payload["shortfall"] == 50000


def test_cli_afford_reports_card_access(getaway_home: Path) -> None:
    prefs.init()
    prefs.set_patch({"cards": [{"issuer": "chase", "product": "sapphire-reserve"}]})
    result = CliRunner().invoke(
        afford.afford_cmd,
        ["--program", "united", "--miles", "90000"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    chase = next(p for p in payload["transfer_paths"] if p["bank"] == "chase")
    assert chase["card_access"] == {
        "required": [
            "sapphire-reserve",
            "sapphire-reserve-business",
            "sapphire-preferred",
            "ink-business-preferred",
            "ink-plus",
        ],
        "held": ["sapphire-reserve"],
        "status": "on_file",
    }


def test_cli_afford_unknown_program_exits_no_data(getaway_home: Path) -> None:
    result = CliRunner().invoke(afford.afford_cmd, ["--program", "nope", "--miles", "1000"])
    assert result.exit_code == 4


def test_cli_afford_missing_prefs_uses_neutral_profile(getaway_home: Path) -> None:
    # Skipping onboarding is fine: a missing prefs file reads as an empty profile
    # (balance 0), not an error — load_or_empty tolerates absence.
    result = CliRunner().invoke(afford.afford_cmd, ["--program", "united", "--miles", "90000"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["balance"] == 0
    assert payload["covered"] is False


def test_hotel_program_transfers_generically() -> None:
    # A hotel program is just another balances.programs slug; afford resolves its
    # bank transfer path with no kind-branching (World of Hyatt is Chase-only).
    result = afford.afford("hyatt", 60000, _prefs({}, {"chase": 60000}))
    by_bank = {p["bank"]: p for p in result["transfer_paths"]}
    assert set(by_bank) == {"chase"}
    assert by_bank["chase"] == {
        "bank": "chase",
        "bank_balance": 60000,
        "ratio": "1:1",
        "points_required": 60000,
        "covers": True,
        "note": (
            "Chase transfers in 1,000-point increments, only from an account holding a "
            "transfer-eligible card. Sapphire Reserve transfers 1:1; Sapphire Preferred and "
            "Ink Business Preferred move to 4:3 for applications on/after 2026-06-15, and "
            "existing Sapphire Preferred holders convert from 1:1 to 4:3 on 2026-10-01 "
            "(per Chase, verified 2026-07-13)."
        ),
        "card_access": {
            "required": [
                "sapphire-reserve",
                "sapphire-reserve-business",
                "sapphire-preferred",
                "ink-business-preferred",
                "ink-plus",
            ],
            "held": [],
            "status": "unknown",
        },
    }


def test_hotel_amex_two_to_one_transfer_arithmetic() -> None:
    # Amex → Hilton is 1:2, so 20,000 Hilton points cost 10,000 Membership Rewards.
    result = afford.afford("hilton", 20000, _prefs())
    hilton = next(p for p in result["transfer_paths"] if p["bank"] == "amex")
    assert hilton["ratio"] == "1:2"
    assert hilton["points_required"] == 10000


def test_hotel_program_purchase_prices_from_typical_sale() -> None:
    result = afford.afford("ihg", 50000, _prefs(), include_purchase=True)
    assert result["purchase"] == {
        "rate_cents": 0.5,
        "cost_usd": 250.0,
        "cap_note": None,
    }
