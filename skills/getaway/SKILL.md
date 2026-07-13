---
name: getaway
description: Plans award flights with judgment, backed by seats.aero availability across 26 mileage programs. Triggers on the dense trip ask — "I want to go away for roughly a week... warm, beachy, cheap points tickets for business class... avoid the common places we always go like seoul or tokyo" — and whenever the user wants to plan a flight or trip on points or miles, find award availability between airports or across a region ("west coast to Asia"), get creative with routings (lie flat on points to a hub like NRT with a cash hop onward, an open jaw, two awards stitched across programs), weigh how interesting the layover city is, price a cash positioning flight, compare mileage programs for a route, pull booking links or taxes for an award, or resume a trip already in planning — or mentions seats.aero. Needs a seats.aero Pro API key, from SEATS_AERO_API_KEY or a 1Password reference in ~/.getaway/preferences.json.
allowed-tools: Bash(uv:*), Bash(op:*), Agent, Workflow
---

# getaway

Plan award trips as a travel agent with judgment, not a search box: parse the one-sentence ask, sweep seats.aero, and weigh what the user weighs — balances, seat quality, layover length and how interesting the layover city is, cash-fare anomalies, expiring credits — with per-trip emphasis. The engine is the bundled Python CLI:

```bash
CLI="uv run --project $CLAUDE_PLUGIN_ROOT/cli getaway"
$CLI prefs status
```

Run that `prefs status` once at the main level before anything fans out: the first invocation builds `cli/.venv`, and a cold build inside a subagent looks like a hung agent. Subagent and workflow briefs carry the expanded invocation with the absolute project path (`uv run --project <plugin-root>/cli getaway ...`); `CLAUDE_PLUGIN_ROOT` is not guaranteed in their shells.

## Auth

The CLI resolves the seats.aero Pro key itself: the `SEATS_AERO_API_KEY` environment variable wins when set; otherwise it reads the `op_ref` preference — a 1Password reference like `op://Vault/item/field` — with `op read`. Exit 2 means no key resolved: relay the printed remedy and stop, since nothing works without one.

`prefs status` prints `{"configured": false}` (exit 1) when no balances are on file. Offer the `getaway:onboard` skill ([../onboard/SKILL.md](../onboard/SKILL.md)) before planning — skippable; a decline means planning proceeds without affordability grounding, and balances never gate a search anyway.

## CLI map

`--help` on any group is the canonical command reference; this table only routes to the right group.

| Group | Purpose |
|---|---|
| `prefs` | Durable preferences: `show`, `status`, `set` (JSON patch on stdin), `set-balance`, `set-status`, `credit-add`/`credit-list`/`credit-remove` |
| `trip` | Per-trip memory: `new`, `set` (patch on stdin), `show`, `list`, `log`, `profile`, `resume`, `status`, `current`, `done`, `phase-check`/`phase-done`, `artifact read`/`write`/`list`, `finalize` |
| `search`, `availability`, `routes`, `expand` | seats.aero calls; `search` and `availability` ingest into the cache and, with `--trip`/`--label`, into a sweep artifact |
| `sweep` | Trip sweeps derived from the plan: `sweep plan <slug>`, `sweep run <slug> <label>` |
| `shortlist` | SQL shortlist over a trip's sweep rows: `shortlist run <slug> [--gateway]`, `shortlist onward <slug>` |
| `rank`, `afford`, `quality` | Deterministic re-rank, transfer-first affordability, seat-quality classification |
| `registry` | Packaged reference data: `programs`, `banks`, `transfer-partners`, `regions`, `factors`, `status-earning`, `points-pricing`, `cabins`, `continents` |
| `learnings` | Append-only planning learnings: `add --scope <api\|prefs\|general>`, `list` |
| `quota` | Quota report from recorded call headers; `quota check --floor N` exit-gates |
| `cache` | Zero-quota queries over cached availability: `query`, `stats`, `prune` |

Exit codes: 0 ok, 1 negative predicate (`prefs status`, `quota check`, `trip phase-check`), 2 auth, 3 state conflict, 4 no data, 64 usage.

## Trip memory

Every trip owns a directory, `~/.getaway/trips/<slug>/`. `trip.json` is the canonical memory: the verbatim `ask`, `window`, `cabin`, `party`, `vibe`, `avoid_final_destinations`, the `plan` fan-out spec, the `judgment` profile, and the `decisions` log. `~/.getaway/trips/current` is a plain-text slug pointer any agent reads without the CLI.

Sweep JSONL, shortlists, expansions, evidence, and `finalists.json` persist under `artifacts/`, and `checkpoints.json` stamps each workflow phase with input fingerprints and a TTL — all CLI-computed. Editing the window or an avoid list invalidates every dependent phase automatically; an untouched plan stays fresh. That is the resume guarantee: a killed or resumed session re-invokes the same workflow and every fresh phase skips wholesale, spending zero quota.

`trip resume <slug>` is the session-start move on any existing trip: one brief with the trip doc, per-phase freshness, finalists so far, expiring credits, and recent api-scope learnings.

## Orchestration

Climb one rung at a time, only when the rung below cannot express the work:

1. Batch into one call. Comma-list destinations — one `search` covers a whole bucket. Call count beats latency; parallelism buys latency, never extra calls.
2. Parallel subagents (the Agent tool, one message, N calls) for independent one-off lookups: per-destination WebSearch enrichment, per-leg `fli` cash pricing. Each brief carries the absolute CLI invocation, the exact commands, and a compact JSON return shape that includes the `quota_remaining` the agent observed.
3. The Workflow tool for every real planning ask. The shipped `plan-trip.js` holds the whole pipeline — sweep, shortlist, onward, bridge, expand, evidence, assess, rank, finalize — and its intermediates; the conversation holds only finalists.

Invariants on every rung:

- One writer for durable state. `prefs set`/`set-balance`/`set-status`/`credit-add`, `trip set`, and `trip log` run at the main level only. Workflow agents and subagents write nothing under `~/.getaway` except their own artifact via `trip artifact write`. Cross-session races are the CLI's flock problem, not yours.
- Interactive surfaces stay at the main level: cc-present boards and `AskUserQuestion`.
- Fan-out adds zero API calls: parallelize only calls you'd make anyway.

## Quota

Pro keys get 1,000 calls per day, resetting at midnight UTC. Every API command records the response quota headers as events; `quota` reports them without spending a call, and `quota check --floor N` exit-gates a phase. One page with a big `--take` beats `--pages`, since each page is a separate call. Answer follow-ups from `cache query` — zero quota; a new call needs a question the cache cannot answer. After a parallel burst, trust the minimum `quota_remaining` the agents reported, not the on-disk history. Below about 100 remaining, tell the user and stop fanning out.

## Planning a trip

1. Warm up and check config: `$CLI prefs status` at the main level.
2. Resume before creating: `$CLI trip list`, and when an open trip matches the ask, `$CLI trip resume <slug>` and skip everything it already pins. Otherwise parse the dense ask clause by clause; [references/planning.md](references/planning.md) works the canonical sentence.
3. `$CLI learnings list --scope api -n 20`: recent API quirks steer source and sweep choices.
4. Ask what the ask leaves open: one `AskUserQuestion` call, at most 4 questions, concrete options each (window, party, one-way or round trip). Never re-ask a pinned constraint.
5. Create and pin: `$CLI trip new <slug> --ask "<verbatim ask>"` (date-prefixed slug), then patch the trip and its plan in one `trip set`:

   ```bash
   $CLI trip set 2026-09-warm-beachy-week <<'EOF'
   {"window": {"start": "2026-09-06", "end": "2026-09-20", "trip_length_days": 7},
    "cabin": "business", "party": 2, "vibe": ["warm", "beachy"],
    "avoid_final_destinations": ["ICN", "GMP", "NRT", "HND"],
    "plan": {"origins": ["WST"],
             "buckets": [{"name": "asia-beach", "dests": ["ASA"]},
                         {"name": "africa", "dests": ["QAF"]}],
             "hybrid": {"gateways": ["NRT", "HND", "TPE"],
                        "onward_dests": ["OKA", "USM"], "max_hybrids": 4},
             "mileage_ceiling": 110000, "max_finalists": 6, "round_trip": true}}
   EOF
   ```

6. Review the judgment profile: `$CLI trip profile <slug>` derives per-factor tiers from the ask and prefs. Where it disagrees with the user's emphasis, patch through another `trip set` with a `judgment` key — free-text `guidance` plus per-factor `{"priority": "primary"|"secondary"|"note"}`.
7. Dispatch:

   ```
   Workflow({
     scriptPath: "${CLAUDE_PLUGIN_ROOT}/skills/getaway/plan-trip.js",
     args: {project: "<absolute path to plugin-root>/cli", slug: "2026-09-warm-beachy-week"}
   })
   ```

   `args.project` is the absolute path to the CLI project directory. Optional: `refresh: true` forces stamped phases to re-run; `quotaFloor` overrides the gate below which API-spending phases skip. Everything else the workflow needs is already on disk.
8. Read finalists from disk, never from the workflow return: `$CLI trip artifact read <slug> finalists.json`.
9. Present the board with one evidence line per active factor per finalist, per [references/planning.md](references/planning.md), "Presenting options".
10. Log every decision as it lands: `$CLI trip log <slug> "picked the CPT stitch; QR over EK for the DOH stop"`. Pin new constraints with `trip set` the moment they're pinned, mid-planning, never at wrap-up. Route always-true facts ("never through IST", a balance correction) to `prefs set`/`set-balance`/`set-status`/`credit-add` right then, and API quirks to `learnings add --scope api`; the plugin's Stop hook (`hooks/reflect.py`) backstops the sweep at session end.

A round trip takes a second Workflow dispatch for the return after the outbound settles — never one combined run.

## References

- [references/planning.md](references/planning.md) — parsing the ask, origin expansion, region sweeps, season awareness, affordability, returns, routing shapes, and presentation.
- [references/doctrine.md](references/doctrine.md) — the settled rulings the whole pipeline obeys.
- [docs/seats-aero-api.md](../../docs/seats-aero-api.md) — the raw Partner API surface, shapes, and program coverage.
