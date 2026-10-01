from __future__ import annotations

from captain_hook import Allow, Block, Event, Input, T, UsedSkill, gate

REFLECT = (
    "Before stopping, land each durable fact the user stated: `getaway prefs set` for lasting preferences, "
    "`getaway trip set` for this trip, `getaway learnings add --scope api` for API quirks. "
    "The routing rules are in `references/reflect.md` of the getaway skill."
)

gate(
    REFLECT,
    only_if=[UsedSkill("getaway:getaway", "getaway:onboard", "getaway:refresh", scope="session")],
    events=Event.Stop,
    max_fires=1,
    tests={
        Input(transcript=[T.assistant(T.tool("Skill", skill="getaway:getaway", args="SFO to Tokyo in September"))]): Block(
            pattern=r"(?s)prefs set.*trip set.*learnings add"
        ),
        Input(transcript=[T.assistant(T.tool("Skill", skill="getaway:refresh", args="refresh my balances"))]): Block(
            pattern=r"(?s)prefs set.*trip set.*learnings add"
        ),
        Input(transcript=[T.assistant(T.tool("Skill", skill="getaway:onboard"))]): Block(
            pattern=r"(?s)prefs set.*trip set.*learnings add"
        ),
        Input(
            transcript=[T.assistant(T.tool("Bash", command="uv run --project cli getaway search --origin SFO --dest NRT"))]
        ): Allow(),
        Input(transcript=[T.assistant(T.tool("Bash", command="git status"))]): Allow(),
        Input(transcript=[]): Allow(),
    },
)
