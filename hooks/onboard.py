from __future__ import annotations

import json
import os
from pathlib import Path

from captain_hook import Allow, Event, Input, Warn, nudge

ONBOARD = (
    "This session is using the getaway skill and ~/.getaway/preferences.json is not configured yet "
    "(the file is missing, or it records no points balances). Before planning a trip, offer the "
    "first-run onboarding form described in the getaway SKILL.md: build the cc-present preferences "
    "form, invoke cc-present:present with it, and on submit write the answers with "
    "getaway.sh prefs-set. The user may skip onboarding and plan with the current defaults; do not "
    "block on it. If preferences are already configured, ignore this."
)

PREFS = Path(os.path.expanduser("~/.getaway/preferences.json"))


def prefs_unconfigured() -> bool:
    if not PREFS.exists():
        return True
    balances = json.loads(PREFS.read_text()).get("balances", {})
    return not balances.get("programs") and not balances.get("transferable")


GETAWAY_SKILL = {
    "type": "assistant",
    "message": {
        "content": [
            {
                "type": "tool_use",
                "name": "Skill",
                "id": "k1",
                "input": {"skill": "getaway:getaway", "args": "SFO to Tokyo in September on points"},
            }
        ]
    },
}

GIT_STATUS = {
    "type": "assistant",
    "message": {
        "content": [
            {
                "type": "tool_use",
                "name": "Bash",
                "id": "g1",
                "input": {"command": "git status"},
            }
        ]
    },
}

nudge(
    ONBOARD,
    when=lambda evt: evt.ctx.t.has_skill("getaway", "getaway:getaway") and prefs_unconfigured(),
    events=Event.PostToolUse,
    max_fires=1,
    tests={
        Input(transcript=[GETAWAY_SKILL]): Warn(pattern=r"onboarding"),
        Input(transcript=[GIT_STATUS]): Allow(),
        Input(transcript=[]): Allow(),
    },
)
