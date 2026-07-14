import sys

import click

from getaway.afford import afford_cmd
from getaway.awardwallet import awardwallet_group
from getaway.bridge import bridge_cmd
from getaway.constants import EXIT_USAGE
from getaway.factors import rank_cmd
from getaway.journeys import expand_group
from getaway.learnings import learnings_group
from getaway.prefs import prefs_group
from getaway.quality import quality_group
from getaway.registry import registry_group
from getaway.seats import availability_cmd, expand_cmd, routes_cmd, search_cmd
from getaway.shortlist import shortlist_group
from getaway.stays import stays_group
from getaway.store import cache_group, quota_cmd
from getaway.sweeps import sweep_group
from getaway.trips import trip_group

# The single-availability expand lives under the group as `expand detail`; `expand run` composes
# journeys from the leg shortlists.
expand_group.add_command(expand_cmd, name="detail")


@click.group()
def cli() -> None:
    """Plan award flights backed by seats.aero availability."""


for group in (
    prefs_group,
    trip_group,
    registry_group,
    cache_group,
    learnings_group,
    quality_group,
    sweep_group,
    shortlist_group,
    expand_group,
    stays_group,
    awardwallet_group,
):
    cli.add_command(group)

for command in (
    quota_cmd,
    afford_cmd,
    search_cmd,
    availability_cmd,
    routes_cmd,
    rank_cmd,
    bridge_cmd,
):
    cli.add_command(command)


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
