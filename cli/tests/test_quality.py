import json

import pytest
from click.testing import CliRunner

from getaway import quality, registry


@pytest.mark.parametrize(
    ("airline", "aircraft", "cabin", "verdict", "product", "matched"),
    [
        pytest.param(
            "BA",
            "Boeing 777-300ER",
            "business",
            "suite",
            "Club Suite",
            "777-300ER",
            id="exact-substring-hit",
        ),
        pytest.param(
            "QR",
            "Airbus A350-1000",
            "business",
            "suite",
            "Qsuite",
            "A350-1000",
            id="coarse-aircraft-string-hit",
        ),
        pytest.param(
            "AA",
            "Boeing 787-9P",
            "business",
            "suite",
            "Flagship Suite",
            "787-9P",
            id="longest-match-wins-over-787-9",
        ),
        pytest.param(
            "AA",
            "Boeing 787-9",
            "business",
            "solid",
            "Super Diamond / Concept D",
            "787-9",
            id="shorter-match-when-longer-absent",
        ),
        pytest.param(
            "EY",
            "Boeing 777-300ER",
            "business",
            "dated",
            "Solstys staggered",
            "777-300ER",
            id="airline-filter-picks-etihad",
        ),
        pytest.param(
            "af",
            "airbus a350-900",
            "business",
            "suite",
            "Door suite",
            "A350-900",
            id="case-insensitive-airline-and-aircraft",
        ),
    ],
)
def test_classify_matrix(
    airline: str,
    aircraft: str,
    cabin: str,
    verdict: str,
    product: str,
    matched: str,
) -> None:
    result = quality.classify(airline, aircraft, cabin)
    assert result["verdict"] == verdict
    assert result["product"] == product
    assert result["matched"] == matched


def test_unknown_carrier_returns_verify_never_a_guess() -> None:
    assert quality.classify("XX", "Boeing 747-400") == {
        "verdict": "verify",
        "product": None,
        "note": None,
        "matched": None,
    }


def test_unknown_aircraft_for_known_carrier_returns_verify() -> None:
    assert quality.classify("BA", "Boeing 737 MAX 8")["verdict"] == "verify"


def test_wrong_cabin_finds_no_row() -> None:
    assert quality.classify("BA", "Boeing 777-300ER", cabin="first")["matched"] is None


def test_file_order_breaks_equal_length_ties(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "airline": "ZZ",
            "aircraft_match": "A350",
            "cabin": "business",
            "product": "First",
            "verdict": "solid",
        },
        {
            "airline": "ZZ",
            "aircraft_match": "A359",
            "cabin": "business",
            "product": "Second",
            "verdict": "suite",
        },
    ]
    monkeypatch.setattr(registry, "seat_quality", lambda: rows)
    # "A350" appears before "A359"; both length 4 and both substrings — file order wins.
    assert quality.classify("ZZ", "widebody A350 A359")["product"] == "First"


def test_list_rows_filters_by_airline() -> None:
    rows = quality.list_rows("UA")
    assert rows
    assert {row["airline"] for row in rows} == {"UA"}
    assert len(quality.list_rows()) == len(registry.seat_quality())


def test_cli_classify_emits_compact_json() -> None:
    result = CliRunner().invoke(
        quality.quality_group,
        ["classify", "--airline", "QR", "--aircraft", "Boeing 777-300ER"],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["verdict"] == "verify"
