# getaway Development Guide

Plan award flights from Claude Code, backed by seats.aero availability across 26 mileage programs.

## Repository Structure

```
getaway/
├── .claude-plugin/           # Plugin + marketplace manifests (install as getaway@getaway)
├── skills/
│   └── getaway/              # The flight-planning skill (SKILL.md + getaway.sh helper)
├── hooks/                    # Plugin-shipped capt-hook pack (hooks.json + reflect.py + onboard.py)
├── docs/
│   ├── assets/               # Mascot logo, README banner, social card
│   └── seats-aero-api.md     # seats.aero Partner API reference
├── capt-hook.toml            # Pack manifest for the plugin-shipped hooks
├── AGENTS.md                 # This file — shared conventions
└── README.md                 # Project overview
```
