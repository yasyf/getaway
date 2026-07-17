---
name: getaway
description: Plans award trips with judgment — flights across 28 mileage programs via seats.aero, hotel award stays via rooms.aero. Triggers on the dense trip ask — "I want to go away for roughly a week... warm, beachy, cheap points tickets for business class... avoid the common places we always go like seoul or tokyo" — and whenever the user wants to plan a flight or trip on points or miles, find award availability between airports or across a region ("west coast to Asia"), plan a round trip or open jaw in one pass, get creative with routings (lie flat on points to a hub like NRT with a cash hop onward, two awards stitched across programs), find hotel award nights at the destination, weigh how interesting the layover city is, price a cash positioning flight, compare mileage programs for a route, pull booking links or taxes for an award, or resume a trip already in planning — or mentions seats.aero or rooms.aero. Needs a seats.aero Pro API key, from SEATS_AERO_API_KEY or a 1Password reference in ~/.getaway/preferences.json.
allowed-tools: Bash(uv:*), Bash(op:*), Agent, Workflow
---

# getaway

Plan award trips as a travel agent with judgment, not a search box: parse the one-sentence ask, sweep seats.aero, compose whole journeys — outbound, return, cash hops, hotel nights — and weigh what the user weighs — balances, seat quality, layover length and how interesting the layover city is, cash-fare anomalies, expiring instruments — with per-trip emphasis. The engine is the bundled Python CLI:

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
| `prefs` | Durable preferences: `show`, `status`, `set` (JSON patch on stdin), `set-balance`, `set-status`, `instrument-add` (JSON on stdin)/`instrument-list`/`instrument-remove` |
| `trip` | Per-trip memory: `new`, `set` (patch on stdin), `show`, `list`, `log`, `profile`, `resume`, `status`, `current`, `done`, `compile`/`explain` (the node graph), `phase-check`/`phase-done`, `artifact read`/`write`/`list`, `finalize` |
| `search`, `availability`, `routes` | Raw seats.aero calls; `search` and `availability` ingest into the cache |
| `sweep` | Leg sweeps derived from the plan: `sweep plan <slug>`, `sweep run <slug> <key>` — keys are `<leg-id>[:<label>]` (e.g. `outbound:asia`, `hop`, `return`) |
| `shortlist` | Shortlist over a leg's sweep rows: `shortlist run <slug> --leg <leg-id>`, `shortlist onward <slug> --leg <leg-id>` |
| `expand` | Journey composition: `expand run <slug>` (expand candidates, pair legs, write journeys), `expand detail <id> --cabin <c>` (one live trip) |
| `bridge` | Cash-leg pricing through the hardened fli driver: `bridge <slug> --leg <leg-id>` |
| `stays` | rooms.aero lodging: `stays intervals <slug>` (the per-journey worklist), `stays ingest <slug>` (normalized rows on stdin) |
| `rank`, `afford`, `quality` | Lane-based rank over assess verdicts, transfer-first affordability, seat-quality classification |
| `enhance` | Background verification primitives: `targets <slug> verify` (the enumerated worklist), `merge <slug> verify` (concurrency-safe result upsert, JSON array on stdin) |
| `registry` | Packaged reference data: `programs [--kind airline\|hotel]`, `banks`, `hosts` (browser-read host list with auth classes), `transfer-partners`, `card-products`, `regions`, `factors`, `status-earning`, `points-pricing`, `cabins`, `continents` |
| `learnings` | Append-only planning learnings: `add --scope <api\|prefs\|general>`, `list` |
| `quota` | Quota report from recorded call headers; the floor enforces at the client — every API-spending command takes `--quota-floor N` |
| `cache` | Zero-quota queries over cached availability: `query`, `stats`, `prune` |

Exit codes: 0 ok, 1 negative predicate (`prefs status`, `trip phase-check`) or a quota-floor stop on an API-spending command, 2 auth, 3 state conflict, 4 no data, 64 usage.

## Trip memory

Every trip owns a directory, `~/.getaway/trips/<slug>/`. `trip.json` is the canonical memory: the verbatim `ask`, `window`, `cabin`, `party`, `vibe`, `avoid_final_destinations`, the `plan` — its ordered `legs` (each an intent), the `preferences` and `constraints` branches, and optional `sources` and `lodging` — the `judgment` profile, and the `decisions` log. `~/.getaway/trips/current` is a plain-text slug pointer any agent reads without the CLI.

Artifacts namespace by leg — `legs/outbound/sweep-<label>.json`, `legs/return/shortlist.json` — with the journey-scoped ones (`expand.json`, `assess.json`, `stays.json`, `finalists.json`) beside them, and `checkpoints.json` stamps every graph node with input fingerprints and a TTL, all CLI-computed. Editing the window or an avoid list invalidates exactly the dependent nodes; an untouched plan stays fresh. That is the resume guarantee: a killed or resumed session re-runs the same walker and every fresh node skips wholesale, spending zero quota.

`trip resume <slug>` is the session-start move on any existing trip: one brief with the trip doc, per-node freshness, finalists so far, expiring instruments, and recent api-scope learnings.

## Orchestration

Climb one rung at a time, only when the rung below cannot express the work:

1. Batch into one call. Comma-list destinations — one `search` covers a whole bucket. Call count beats latency; parallelism buys latency, never extra calls.
2. Parallel subagents (the Agent tool, one message, N calls) for independent one-off lookups: per-destination WebSearch enrichment, a one-off cash quote. Each brief carries the absolute CLI invocation and the exact commands.
3. The Workflow tool for every real planning ask. The CLI is the toolbox and `trip compile <slug>` is the contract: it derives the trip's node graph — per-node ready-made commands, artifact dependencies, freshness, quota costs, `requires`, and model routing — and `trip explain <slug>` prints it. The shipped `plan-trip.js` is the codified reference walker for the canonical ask, well-fought but not mandatory; a non-canonical ask (multi-city, positioning-led, a partial refresh) authors its own walker per [references/workflows.md](references/workflows.md).

Routing is enforced, not suggested: every compiled node carries `{model, effort}`, and a walker passes it into each `agent()` call. Mechanical node-runners — emitted commands, JSON shaping — run sonnet at low effort; single-fact labeling runs haiku; research and judgment — the evidence collectors, assess, the stays browser walk — run opus at xhigh, or gpt-5.6-terra through the `codex:codex-wrapper` agent type (`researchLane: "terra"`). Fable never runs a trip-planning subagent.

Invariants on every rung:

- One writer for durable state. `prefs set`/`set-balance`/`set-status`/`instrument-add`, `trip set`, and `trip log` run at the main level only. Workflow agents and subagents write nothing under `~/.getaway` except their own artifact via `trip artifact write` (`stays ingest` for the walk, `enhance merge` for background verifiers). Cross-session races are the CLI's flock problem, not yours.
- Interactive surfaces stay at the main level: cc-present boards and `AskUserQuestion`.
- Fan-out adds zero API calls: parallelize only calls you'd make anyway.

## Quota

Pro keys get 1,000 calls per day, resetting at midnight UTC. The floor enforces at the client: every API call reserves a unit before the request and reconciles the response header after, so parallel fan-outs cannot jointly cross it. `--quota-floor N` rides every API-spending command (default 100; `0` is a deliberate spend-down), and a floor stop exits 1 with the work recorded as `not_run` — distinct from a data failure, and resumable once quota returns. One page with a big `--take` beats `--pages`, since each page is a separate call. Answer follow-ups from `cache query` — zero quota; a new call needs a question the cache cannot answer. Stays spend zero seats.aero quota. When the engine starts refusing at the floor, tell the user rather than lowering it.

## Invent the shape, then price it

The founding instruction, verbatim: "the point is we can get creative with routings". The plan is `plan.legs` — an ordered list of leg-intents — and a journey is any chain of concrete legs satisfying them. There is no trip-type enum: a round trip, an open jaw, a multi-city stitch, a positioning hop, and a shape nobody has named yet all compile through the same fold. Parse the ask into the shape the user actually described, then let every shape compete as a journey — pre-filtering is the search box this skill exists to replace.

The leg-intent primitives:

| Primitive | What it expresses |
|---|---|
| `origins` / `dests` | Endpoints: IATA codes, region pseudo-codes, `"$origins"` as the whole dests value on the homeward leg. Omitted origins chain from wherever the prior leg lands; explicit endpoints on a later leg are an open jaw and REPLACE the chained anchor. |
| `mode: award\|cash\|either` | Which pricing lanes the leg rides: award sweeps, the cash bridge, or both competing. |
| `stay_nights: {min, max}` | Marks a stop and bounds the next leg's derived window; each stay-marked boundary becomes a lodging interval when `plan.lodging` is in scope. |
| `optional: true` | The leg fans into with/without variants competing on one front — a positioning hop surfaces beside its home-origin-direct alternative, priced, never assumed. |
| `buckets` / `program_sweeps` | Sweep groupings on an award leg: named dest buckets, per-program region sweeps. |
| `dests: {discover: {brief, max_airports}}` | Judgment picks the endpoints: a zero-quota scout node researches the brief, and its validated airports feed the leg's sweep beside anything declared. |
| `window` | Per-leg date bounds; omitted windows derive from the prior leg's observed arrivals plus its stay. |
| `plan.tuning` | Per-trip knob overrides — presentation limit, expansion budget per endpoint, beam width, sweep page budget, date padding — when the ask warrants wider or thriftier search than the defaults. |
| `legs/manual.json` | The escape hatch: declare explicit candidate chains by hand — availability ids plus cash pairs — and `expand` prices them through the same deterministic fit, cost, and miss lanes as composed journeys. |

The downstream stages — sweep, shortlist, pairs/bridge, expand, assess, rank, stays, enhance — are à la carte primitives: `trip compile` derives which of them any shape needs, and each is a CLI command an agent can run alone (a lone `sweep run` answering "any J space to Tokyo in March?" is a legitimate use of this skill). The compiled graph is the default orchestration, not the only one.

## The canonical walk

The dense canonical ask — warm, beachy, business, avoid the usual — runs end to end below. It is one worked example in conventional ids (`outbound`/`return`), not a mandate: a different shape pins different legs at step 5, and when `plan-trip.js` cannot express the walk, author one per [references/workflows.md](references/workflows.md).

1. Warm up and check config: `$CLI prefs status` at the main level.
2. Resume before creating: `$CLI trip list`, and when an open trip matches the ask, `$CLI trip resume <slug>` and skip everything it already pins. Otherwise parse the dense ask clause by clause; [references/planning.md](references/planning.md) works the canonical sentence.
3. `$CLI learnings list --scope api -n 20`: recent API quirks steer source and sweep choices.
4. Ask what the ask leaves open: one `AskUserQuestion` call, at most 4 questions, concrete options each (window, party, one-way or round trip, lodging in scope). A hard deadline becomes a `constraints` entry only after the user confirms it — everything else is a preference. Never re-ask a pinned constraint.
5. Create and pin: `$CLI trip new <slug> --ask "<verbatim ask>"` (date-prefixed slug), then patch the trip and its plan in one `trip set`:

   ```bash
   $CLI trip set 2026-09-warm-beachy-week <<'EOF'
   {"window": {"start": "2026-09-06", "end": "2026-09-20", "trip_length_days": null},
    "cabin": "business", "party": 2, "vibe": ["warm", "beachy"],
    "avoid_final_destinations": ["ICN", "GMP", "NRT", "HND"],
    "plan": {"legs": [{"id": "outbound", "origins": ["WST"],
                       "buckets": [{"name": "asia-gateway",
                                    "dests": ["NRT", "HND", "TPE"]}]},
                      {"id": "beach-hop", "mode": "either",
                       "dests": ["OKA", "USM"], "stay_nights": {"min": 6, "max": 8}},
                      {"id": "return", "dests": "$origins"}],
             "preferences": {
               "cabin": {"value": "business", "priority": "primary"},
               "trip_length": {"value": {"days": 7, "basis": "elapsed_door_to_door"},
                               "priority": "secondary"},
               "mileage_target": {"value": {"miles": 110000, "scope": "per_person_per_leg"},
                                  "priority": "primary"}},
             "lodging": {}}}
   EOF
   ```

   `preferences` order and annotate — the model trades them off, and every miss surfaces named on the board. `constraints` gate, and hold only what the user explicitly confirmed (each entry carries `confirmed: true`). `lodging: {}` puts hotel stays in scope; an explicit `checkout` belongs in it on an open jaw.

6. Review the judgment profile: `$CLI trip profile <slug>` derives per-factor tiers from the ask and prefs. Where it disagrees with the user's emphasis, patch through another `trip set` with a `judgment` key — free-text `guidance` plus per-factor `{"priority": "primary"|"secondary"|"note"}`.
7. Compile and inspect: `$CLI trip compile <slug>`, then `$CLI trip explain <slug>` — the node graph, per-node staleness and commands, the quota budget, and any `requires`. When `plan.lodging` is in scope, seed the rooms.aero browser session BEFORE dispatch: one main-level `cookiesync auth` tap — its `--reason` naming rooms.aero plus the cookie-class hosts step 10's verifiers will read, so one informed tap covers both — then `abwc-seed --session rooms rooms.aero seats.aero`. The walker preflights the session and fails loudly without it.
8. Dispatch:

   ```
   Workflow({
     scriptPath: "${CLAUDE_PLUGIN_ROOT}/skills/getaway/plan-trip.js",
     args: {project: "<absolute path to plugin-root>/cli", slug: "2026-09-warm-beachy-week"}
   })
   ```

   `args.project` is the absolute path to the CLI project directory. Optional: `refresh: true` forces stamped sweep nodes to re-run; `quotaFloor` overrides the client gate; `researchLane: "terra"` routes the judgment agents through gpt-5.6-terra. Everything else the walker needs is already on disk — one dispatch covers the outbound, the return, hybrids, and stays.
9. Read finalists from disk, never from the workflow return: `$CLI trip artifact read <slug> finalists.json`.
10. Kick off background verification per [references/enhancers.md](references/enhancers.md): `$CLI enhance targets <slug> verify` enumerates the rows worth a live check — unknown seat sufficiency, stale cached reads, unverified empty leads. When targets exist, group by auth lane, prime the cookie grant only if step 7's tap didn't cover it, spawn the verifiers as background subagents (sonnet — mechanical search-and-extract) in one message, and move on: the board never waits for them. On each completion notification, fold — `$CLI rank <slug> && $CLI trip finalize <slug>` — and update the live board: "verified live HH:MM" beside the freshness stamp, a prominent gone-on-`<host>` flag on a killed row. The row stays visible, and when it was the user's pick, tell them immediately.
11. Present the journey board — ranked journeys, notable stretches, unpaired leads, search states, stays — with one evidence line per active factor and every preference miss named, per [references/planning.md](references/planning.md), "Presenting options".
12. Log every decision as it lands: `$CLI trip log <slug> "picked the CPT stitch; QR over EK for the DOH stop"`. Pin new constraints with `trip set` the moment they're pinned, mid-planning, never at wrap-up. Route always-true facts ("never through IST", a balance correction) to `prefs set`/`set-balance`/`set-status`/`instrument-add` right then, and API quirks to `learnings add --scope api`; the plugin's Stop hook (`hooks/reflect.py`) backstops the sweep at session end.

## References

- [references/planning.md](references/planning.md) — parsing the ask, origin expansion, region sweeps, season awareness, affordability, journeys, routing shapes, hotels, and presentation.
- [references/workflows.md](references/workflows.md) — authoring an ad-hoc walker over the compiled graph.
- [references/enhancers.md](references/enhancers.md) — fire-and-forget background verification: the contract, auth lanes, and the fold.
- [references/doctrine.md](references/doctrine.md) — the settled rulings the whole pipeline obeys.
- [docs/seats-aero-api.md](../../docs/seats-aero-api.md) — the raw Partner API surface, shapes, and program coverage.
