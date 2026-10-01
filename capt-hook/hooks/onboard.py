from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, Event, Input, T, Warn, nudge

CLI_DIR = Path(__file__).resolve().parent.parent.parent / "cli"

ONBOARD = (
    "Travel preferences are not configured yet. "
    "Invoke `/getaway:onboard` to run first-run onboarding before planning a trip."
)


def prefs_unconfigured() -> bool:
    # WORKAROUND: CLAUDE_PLUGIN_ROOT is unset under `uvx capt-hook run`, so the CLI path resolves from this file.
    result = subprocess.run(
        ["uv", "run", "--project", str(CLI_DIR), "getaway", "prefs", "status"],
        capture_output=True,
    )
    return result.returncode != 0


nudge(
    ONBOARD,
    when=lambda evt: evt.ctx.t.has_skill("getaway", "getaway:getaway", "getaway:refresh") and prefs_unconfigured(),
    events=Event.PostToolUse,
    max_fires=1,
    tests={
        Input(transcript=[T.assistant(T.tool("Skill", skill="getaway:getaway", args="SFO to Tokyo in September"))]): Warn(
            pattern=r"getaway:onboard"
        ),
        Input(transcript=[T.assistant(T.tool("Skill", skill="getaway:refresh", args="refresh my balances"))]): Warn(
            pattern=r"getaway:onboard"
        ),
        Input(transcript=[T.assistant(T.tool("Skill", skill="getaway:onboard"))]): Allow(),
        Input(transcript=[T.assistant(T.tool("Bash", command="git status"))]): Allow(),
        Input(transcript=[]): Allow(),
    },
)
