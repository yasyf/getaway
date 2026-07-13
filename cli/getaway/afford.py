import math

import click

from getaway import prefs, registry
from getaway.paths import map_errors


def _parse_ratio(ratio: str) -> tuple[float, float]:
    a, b = ratio.split(":")
    return float(a), float(b)


def _purchase(program: str, shortfall: int) -> dict:
    pricing = registry.points_pricing()[program]
    rate = pricing["typical_sale_cents"]
    if rate is None:
        rate = pricing["buy_rate_cents"]
    if rate is None:
        reason = (
            f"{program} does not sell points"
            if not pricing["sells_points"]
            else f"no public buy rate for {program}"
        )
        return {"rate_cents": None, "cost_usd": None, "cap_note": None, "reason": reason}
    cap = pricing["annual_cap"]
    cap_note = (
        f"{shortfall} exceeds {program} annual purchase cap of {cap}"
        if cap is not None and shortfall > cap
        else None
    )
    return {
        "rate_cents": rate,
        "cost_usd": round(shortfall * rate / 100, 2),
        "cap_note": cap_note,
    }


def afford(
    program: str, miles_needed: int, prefs_doc: dict, include_purchase: bool = False
) -> dict:
    balances = prefs_doc.get("balances", {})
    balance = balances.get("programs", {}).get(program, 0)
    bank_balances = balances.get("transferable", {})
    shortfall = max(0, miles_needed - balance)

    transfer_paths = []
    for bank, paths in registry.transfer_partners().items():
        for entry in paths:
            if entry["program"] != program:
                continue
            a, b = _parse_ratio(entry["ratio"])
            points_required = math.ceil(shortfall * a / b)
            increment = entry["increment"]
            if increment:
                points_required = math.ceil(points_required / increment) * increment
            bank_balance = bank_balances.get(bank, 0)
            transfer_paths.append(
                {
                    "bank": bank,
                    "bank_balance": bank_balance,
                    "ratio": entry["ratio"],
                    "points_required": points_required,
                    "covers": bank_balance >= points_required,
                }
            )

    return {
        "program": program,
        "miles_needed": miles_needed,
        "balance": balance,
        "covered": balance >= miles_needed,
        "shortfall": shortfall,
        "transfer_paths": transfer_paths,
        "purchase": _purchase(program, shortfall) if include_purchase and shortfall > 0 else None,
    }


@click.command("afford")
@click.option("--program", required=True, help="Target mileage program slug.")
@click.option("--miles", "miles_needed", type=int, required=True, help="Miles the award needs.")
@click.option("--include-purchase", is_flag=True, help="Price buying the shortfall.")
@map_errors
def afford_cmd(program: str, miles_needed: int, include_purchase: bool) -> None:
    if not registry.is_program(program):
        raise registry.ExitNoData(f"unknown program {program}")
    registry.emit(afford(program, miles_needed, prefs.load_or_empty(), include_purchase))
