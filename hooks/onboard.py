from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, Event, Input, Warn, nudge

CLI_DIR = Path(__file__).resolve().parent.parent / "cli"

ONBOARD = (
    "This session is using the getaway skill but travel preferences are not configured yet "
    "(`getaway prefs status` reports unconfigured — the preferences file is missing, or it records "
    "no points balances). Before planning a trip, offer first-run onboarding by invoking the "
    "/getaway:onboard skill (Skill tool), which runs the auto-fill gatherers as parallel subagents "
    "(Gmail via gog, plus one browser gatherer per airline and bank host via "
    "agent-browser-with-cookies), seeds a cc-present preferences form, and writes only on the "
    "form's Submit — landing balances through `getaway prefs set-balance`, statuses through "
    "`getaway prefs set-status`, and the rest through a `getaway prefs set` stdin patch. The user "
    "may skip onboarding and plan with the current defaults; do not block on it. If preferences are "
    "already configured, ignore this."
)


def prefs_unconfigured() -> bool:
    # exit 0 = configured; 1 (negative) and 3 (state-conflict / missing prefs) are both the pre-init
    # unconfigured state. CLI path resolves from this file, not CLAUDE_PLUGIN_ROOT, which is unset
    # under `uvx capt-hook run`.
    result = subprocess.run(
        ["uv", "run", "--project", str(CLI_DIR), "getaway", "prefs", "status"],
        capture_output=True,
    )
    return result.returncode != 0


def skill_use(skill: str, args: str, uid: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Skill", "id": uid, "input": {"skill": skill, "args": args}}
            ]
        },
    }


def bash_use(command: str, uid: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Bash", "id": uid, "input": {"command": command}}]
        },
    }


nudge(
    ONBOARD,
    when=lambda evt: evt.ctx.t.has_skill("getaway", "getaway:getaway", "getaway:refresh")
    and prefs_unconfigured(),
    events=Event.PostToolUse,
    max_fires=1,
    tests={
        Input(transcript=[skill_use("getaway:getaway", "SFO to Tokyo in September on points", "k1")]): Warn(
            pattern=r"getaway:onboard"
        ),
        Input(transcript=[skill_use("getaway:refresh", "refresh my balances", "k2")]): Warn(
            pattern=r"getaway:onboard"
        ),
        Input(transcript=[skill_use("getaway:onboard", "", "k3")]): Allow(),
        Input(transcript=[bash_use("git status", "g1")]): Allow(),
        Input(transcript=[]): Allow(),
    },
)
