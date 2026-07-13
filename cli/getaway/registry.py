import json
from functools import cache
from importlib.resources import files
from typing import Any

import click

from getaway.constants import CABIN_PREFIX, CONTINENTS, EXIT_NO_DATA


class NoData(Exception):
    """A requested registry entry has no packaged data."""


class ExitNoData(click.ClickException):
    exit_code = EXIT_NO_DATA


@cache
def _load(name: str) -> Any:
    return json.loads((files("getaway.data") / f"{name}.json").read_text())


def programs() -> dict:
    return _load("programs")


def banks() -> dict:
    return _load("banks")


def transfer_partners() -> dict:
    return _load("transfer_partners")


def seat_quality() -> list:
    return _load("seat_quality")


def regions() -> list:
    return _load("regions")


def factors() -> list:
    return _load("factors")["factors"]


def status_earning() -> dict:
    return _load("status_earning")


def points_pricing() -> dict:
    return _load("points_pricing")


def program_slugs() -> list[str]:
    return list(programs())


def is_program(slug: str) -> bool:
    return slug in programs()


def is_bank(slug: str) -> bool:
    return slug in banks()


def region(code: str) -> dict:
    for row in regions():
        if row["code"] == code:
            return row
    raise NoData(f"unknown region code {code}")


def expand_region(code: str) -> list[str]:
    airports = region(code)["airports"]
    if airports is None:
        raise NoData(f"region {code} has no local airport list")
    return airports


def factor_ids() -> list[str]:
    return [f["id"] for f in factors()]


def emit(obj: object) -> None:
    click.echo(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


registry_group = click.Group("registry", help="Packaged reference registries.")


@registry_group.command("programs")
@click.option("--seats-aero", is_flag=True, help="Only seats.aero-backed programs.")
@click.option("--domains", is_flag=True, help="Emit slug to domains instead of full rows.")
def _programs(seats_aero: bool, domains: bool) -> None:
    rows = programs()
    if seats_aero:
        rows = {slug: row for slug, row in rows.items() if row["seats_aero"]}
    emit({slug: row["domains"] for slug, row in rows.items()} if domains else rows)


@registry_group.command("banks")
def _banks() -> None:
    emit(banks())


@registry_group.command("transfer-partners")
@click.option("--bank", help="Restrict to one bank.")
@click.option("--program", help="Restrict to paths reaching one program.")
def _transfer_partners(bank: str | None, program: str | None) -> None:
    table = transfer_partners()
    if bank is not None:
        if bank not in table:
            raise ExitNoData(f"unknown bank {bank}")
        table = {bank: table[bank]}
    if program is not None:
        table = {b: [e for e in paths if e["program"] == program] for b, paths in table.items()}
        table = {b: paths for b, paths in table.items() if paths}
        if not table:
            raise ExitNoData(f"no transfer path to {program}")
    emit(table)


@registry_group.command("regions")
def _regions() -> None:
    emit(regions())


@registry_group.command("factors")
def _factors() -> None:
    emit(factors())


@registry_group.command("status-earning")
@click.option("--program", help="Restrict to one program.")
def _status_earning(program: str | None) -> None:
    table = status_earning()
    if program is not None:
        if program not in table:
            raise ExitNoData(f"no status-earning data for {program}")
        table = {program: table[program]}
    emit(table)


@registry_group.command("points-pricing")
@click.option("--program", help="Restrict to one program.")
def _points_pricing(program: str | None) -> None:
    table = points_pricing()
    if program is not None:
        if program not in table:
            raise ExitNoData(f"no points-pricing data for {program}")
        table = {program: table[program]}
    emit(table)


@registry_group.command("cabins")
def _cabins() -> None:
    emit(CABIN_PREFIX)


@registry_group.command("continents")
def _continents() -> None:
    emit(list(CONTINENTS))
