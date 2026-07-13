import sys

import click

from getaway.constants import EXIT_USAGE


@click.group()
def cli() -> None:
    """Plan award flights backed by seats.aero availability."""


@cli.group()
def prefs() -> None:
    """Durable travel preferences."""


@cli.group()
def trip() -> None:
    """Per-trip planning memory and artifacts."""


@cli.group()
def registry() -> None:
    """Packaged reference registries."""


@cli.group()
def cache() -> None:
    """Derived SQLite availability cache."""


@cli.group()
def learnings() -> None:
    """Append-only planning learnings."""


def main() -> None:
    try:
        rv = cli.main(standalone_mode=False)
    except click.UsageError as err:
        err.show()
        sys.exit(EXIT_USAGE)
    except click.ClickException as err:
        err.show()
        sys.exit(err.exit_code)
    if isinstance(rv, int):
        sys.exit(rv)
