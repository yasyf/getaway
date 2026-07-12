---
name: getaway
description: Plans award flights using the seats.aero Partner API. Triggers when the user wants to plan an award flight or trip on points or miles, find award availability or saver space between airports or across a region ("west coast to Asia", "somewhere warm in September"), compare mileage programs for a route, pull booking links or taxes for an award, find a cash positioning flight — or mentions seats.aero. Needs a seats.aero Pro API key, from SEATS_AERO_API_KEY or a 1Password reference in ~/.getaway/preferences.json.
allowed-tools: Bash(curl:*), Bash(jq:*), Bash(op:*), Bash(uvx:*), Bash(gog:*), Agent, Workflow
---

# getaway

Plan award flights with cached availability from the
[seats.aero Partner API](https://developers.seats.aero/). Drive every API
call through the bundled helper script,
`"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh"` — always invoke it by
that absolute path, and never hand-compose a `curl` for anything a
subcommand covers. The full API surface, data shapes, and program coverage
live in [docs/seats-aero-api.md](../../docs/seats-aero-api.md).

## Auth and setup

Every request needs a seats.aero Pro API key in the `Partner-Authorization`
header. Keys start with `pro_` and are generated on the seats.aero Settings
page, under the API tab. The script resolves the key itself: the
`SEATS_AERO_API_KEY` environment variable wins when set (the getaway repo's
gitignored `.env` populates it through direnv; elsewhere, export it
yourself); otherwise the script falls back to the `op_ref` preference — a
1Password reference like `op://Vault/item/field`, read with `op read`. If
neither resolves, the script exits 2 and prints the remedy; relay it to the
user and stop, since nothing works without a key.

The base URL is `https://seats.aero/partnerapi`. Pro keys get 1,000 calls
per day, resetting at midnight UTC. Budget them per
[Quota discipline](#quota-discipline).

On first use, run the `getaway:onboard` skill
([skills/onboard/SKILL.md](../onboard/SKILL.md)), which auto-fills the
preferences from Gmail and airline/bank logins and confirms them in a
form. A bare
`prefs-init` only writes the template below.

`prefs` prints the current file as compact JSON, and these are the shipped
defaults:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs
```

```json
{"op_ref":null,"home_airport":"SFO","origin_airports":["SFO","SJC","SAN","PDX","DEN","LAS","SLC","YVR"],"avoid_transit":[],"avoid_airlines":[{"code":"ET","name":"Ethiopian Airlines","strength":"soft"}],"statuses":{},"balances":{"programs":{},"transferable":{}},"learnings":[]}
```

The keys that steer planning:

| Key | Meaning |
|---|---|
| `origin_airports` | Explicit IATA codes; the default origin set |
| `avoid_transit` | Hard drop on connections; enforced against `/trips` segments |
| `avoid_airlines` | `{code, name, strength}` objects; `soft` demotes, `hard` drops, and matching keys on `code` |
| `statuses` | Program slug to elite tier, verbatim (`{"united": "1K"}`); ties on mileage cost break toward these carriers |
| `balances.programs`, `balances.transferable` | Program slug to points; bank currencies |
| `learnings` | Session takeaways; see [Learnings](#learnings) |

Everything here is always-true. Trip-shaped constraints — dates, cabin,
party, regions, destinations to skip — live per trip in
[Trip memory](#trip-memory), never in preferences.

## Command reference

| Command | API calls | Purpose |
|---|---|---|
| `prefs-init` | 0 | Write the `~/.getaway/preferences.json` template; exits 3 if the file exists |
| `prefs` | 0 | Print preferences as compact JSON; exits 3 when the file is missing |
| `prefs-status` | 0 | Print `configured` (exit 0) or `unconfigured` (exit 1: file missing or no balances recorded) |
| `prefs-set` | 0 | Top-level-merge a JSON patch from stdin into preferences, creating the file from the template when absent; exits 64 on unknown keys or non-object input, 3 on a malformed merge |
| `plan-new <slug>` | 0 | Create `~/.getaway/plans/<slug>.json` from the plan template and set it current; exits 64 on a malformed slug, 3 if the slug exists |
| `plan-set [<slug>]` | 0 | Top-level-merge a JSON patch from stdin into the named/current plan; exits 64 on unknown or reserved (`slug`/`created`) keys or non-object input, 3 when no plan resolves or the merge is malformed |
| `plan-show [<slug>]` | 0 | Print the named/current plan as compact JSON; exits 3 when the plan is missing or nothing is current |
| `plan-list` | 0 | One tab-separated `slug status created` row per plan |
| `plan-done [<slug>]` | 0 | Set the plan's `status` to `done`; clears the current pointer when it points there; exits 3 when the plan is missing |
| `search --origin A,B --dest C,D [flags]` | 1 per page | Cached award space via `/search`; origins and destinations take IATA codes or [region pseudo-codes](#region-pseudo-codes) |
| `availability --source <program> [flags]` | 1 per page | Per-program bulk dump via `/availability`; the only route to continent-wide sweeps |
| `trip <availability-ID>` | 1 | One row's bookable trips via `/trips/{id}`: segments, exact taxes, booking links |
| `quota` | 0 | Print the last recorded quota from cache; exits 4 before the first API call |

`search` flags: `--origin`/`--dest` (required, comma lists), `--start`/
`--end` (`YYYY-MM-DD`), `--cabin`, `--sources`, `--carriers`, `--direct`,
`--order lowest_mileage`, `--take` (default 500), `--pages` (default 1).

`availability` flags: `--source` (required, one program slug), `--cabin`,
`--start`/`--end`, `--origin-region`/`--dest-region` (full continent names:
Africa, Asia, Europe, North America, Oceania, South America), `--take`,
`--pages`.

`search` and `availability` emit JSONL — one availability object per line,
deduped by `.ID` across pages — so output pipes straight into `jq` or a
scratchpad file. `--pages` walks the API's `cursor` + `skip` continuation,
and every page costs one quota call: prefer one page with a big `--take`.
Exit codes: 2 no key, 3 preferences or plan problem (missing file, no
current plan), 4 no quota recorded, 64 usage (unknown or reserved patch
keys and malformed plan slugs included).

## Trip memory

Each trip's constraints live in their own file at
`~/.getaway/plans/<slug>.json`, separate from the always-true preferences.
The active slug sits in `~/.getaway/plans/current` — a plain text file, so
subagents read it without the script. `plan-new` creates the file from
this template and points `current` at it:

```json
{
  "slug": null,
  "created": null,
  "status": "planning",
  "ask": null,
  "window": {"start": null, "end": null, "trip_length_days": null},
  "cabin": null,
  "party": 1,
  "regions": {"include": [], "exclude": []},
  "vibe": [],
  "avoid_final_destinations": [],
  "decisions": []
}
```

Field semantics: `ask` holds the user's brief verbatim; `window.start`/
`window.end` are concrete `YYYY-MM-DD` bounds; `regions` uses the
`availability` region vocabulary (Africa, Asia, Europe, North America,
Oceania, South America); `decisions` is an append-only `{date, note}` log
of choices made while planning; a `null` means not yet pinned down — fill
each field the moment the user pins it.

> **Warning:** `avoid_final_destinations` vetoes only where the trip
> *ends*. These airports stay fully valid as connections, layovers, and
> positioning stops — never drop a routing for passing through one. City
> codes are pre-expanded to airports (Seoul is ICN and GMP) so subagents
> match rows without a lookup.

The `plan-set` merge is top-level, same as `prefs-set`: a patch key
replaces that whole key. `window` and `regions` are nested objects, so
always send them whole — a patch carrying only `window.start` erases the
other two window fields. A real write against the current plan:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" plan-set <<'JSON'
{"cabin": "business",
 "window": {"start": "2026-07-12", "end": "2026-07-16", "trip_length_days": 7},
 "regions": {"include": [], "exclude": ["North America"]},
 "vibe": ["warm", "beachy"],
 "avoid_final_destinations": ["ICN", "GMP", "NRT", "HND"]}
JSON
```

```
/Users/<user>/.getaway/plans/2026-07-warm-beachy-week.json
```

Lifecycle: `plan-new` at planning start (after `plan-list` rules out
resuming an open plan), `plan-set` throughout planning as constraints pin
down, `plan-done` once the trip is booked or abandoned — it flips
`status` and clears `current`. Old plans stay on disk as history.

## Orchestration

The flows below fan out by default. Sibling lookups are independent —
nothing about a Lisbon sweep informs an Athens one — so independent
calls never run one after another: fan-out spends the same quota in a
fraction of the wall time. Climb this ladder one rung at a time, only
when the rung below cannot express the work:

1. **Batch into one call.** Comma-list destinations first — one
   `search` call covers a whole bucket. Call count beats latency
   ([Quota discipline](#quota-discipline)); parallelism buys latency,
   never extra calls.
2. **Parallel subagents — the Agent tool, one message, N calls.** The
   default for independent calls that cannot share a batch:
   per-program `availability` sweeps, per-finalist `trip` expansions,
   per-destination `WebSearch` enrichment, per-leg `fli` pricing, the
   onboarding gatherers — Gmail plus one browser gatherer per host
   ([../onboard/SKILL.md](../onboard/SKILL.md)).
   Every brief carries the exact commands to
   run and a compact JSON return shape; a brief that spends API quota
   also carries the absolute `getaway.sh` path, the scratchpad file to
   write, and the `quota remaining` the agent observed in its return
   shape.
3. **The Workflow tool.** A planning ask spanning two or more
   destination buckets or programs runs the shipped `plan-trip.js` —
   the invocation lives in the [Planning workflow](#planning-workflow).
   The script holds the four-phase pipeline — sweep, shortlist,
   expand, enrich — and its intermediate results; the conversation
   holds only the finalists.
4. **A team, for one shape.** A multi-city or multi-traveler plan that
   will span several presentation rounds earns a persistent team
   (`TeamCreate`): a sweeper teammate holds the sweep JSONL and
   observed quota across rounds, re-filtering and expanding on demand
   while the lead drives the board. Everything else stays subagents
   and workflows.

Invariants on every rung:

- Fan-out never adds API calls — parallelize only calls you would make
  anyway.
- Interactive surfaces stay at the main level: cc-present boards and
  forms, `AskUserQuestion`. (Touch ID lands on the user's screen
  whichever agent invokes cookiesync, so the balances gatherers may
  run as subagents; the priming `auth` itself stays at the main level,
  gatherers running only post-prime `cookies` pulls.)
- One writer for durable state: every `prefs-set` and `plan-set` runs
  at the main level. Subagents read `prefs` and the plan file and
  write only their own scratchpad files; nothing under `~/.getaway` is
  theirs to touch.
- The quota cache is last-writer-wins: after a parallel burst, trust
  the minimum your subagents reported, or run `quota` once the burst
  settles — never mid-burst.
- Sequential stays right for a single lookup: one route, one
  expansion, one balance check runs inline.

## Planning workflow

Every planning request starts with a status check:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs-status
```

```
unconfigured
```

`unconfigured` (exit 1) means the preferences file is missing or records
no points balances — and without balances, program selection is
guesswork. Offer the `getaway:onboard` skill
([skills/onboard/SKILL.md](../onboard/SKILL.md)) before planning —
skippable; a decline means planning proceeds on the current defaults.
If no file exists yet, run `prefs-init` on the skip path so `prefs` has
defaults to read. A `PostToolUse` hook (`hooks/onboard.py`, sibling to
`reflect.py`) backstops the offer once per session and never blocks.

1. Read the globals with `prefs`: the origin set, `avoid_transit`,
   `avoid_airlines`, and which programs hold points. When mileage costs
   tie, prefer carriers where `statuses` shows the user holds elite
   status. Preferences carry nothing trip-shaped — no window, cabin, or
   destination derivation happens here.
2. Load or create [trip memory](#trip-memory). `plan-list` first: when an
   open `status: "planning"` plan matches the ask, resume it with
   `plan-show` and skip re-asking what it already pins. Otherwise
   `plan-new <slug>` (date-prefixed, like `2026-07-warm-beachy-week`) and
   `plan-set` the user's brief verbatim into `ask`.
3. Pin down the ask with one `AskUserQuestion` call — up to 4 questions
   (window, cabin, region, one-way or round trip, travelers), concrete
   options each. Skip anything the plan already answers. `plan-set` every
   answer immediately. The standing rule for the whole workflow: the
   moment a constraint is pinned down — mid-planning, not at wrap-up — it
   goes to the plan file via `plan-set`, so resumed sessions and subagents
   read it from disk; anything the user states as always-true ("I never
   fly Ethiopian", "never connect through IST", a balance correction) goes
   to `prefs-set` right then instead.

Steps 4–7 are the fan-out core. When the ask spans two or more
destination buckets or programs, run them as one shot — the shipped
workflow, args assembled from `prefs` and the plan file:

```
Workflow({
  scriptPath: "${CLAUDE_PLUGIN_ROOT}/skills/getaway/plan-trip.js",
  args: {
    script: "${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh",
    scratchpad: "<session scratchpad dir>",
    startDate: "2026-09-08", endDate: "2026-10-08",
    origins: ["SFO", "SJC"], cabin: "business",
    buckets: [{name: "iberia", dests: ["LIS", "BCN", "ATH"]}],
    programSweeps: [{source: "aeroplan", destRegion: "Africa"}],
    sources: ["aeroplan", "alaska"],
    avoidDestinations: ["ICN", "GMP"], avoidTransit: ["IST"],
    avoidAirlines: [{code: "ET", strength: "soft"}],
    mileageCeiling: 90000, travelers: 2, maxFinalists: 6,
    vibe: "warm"
  }
})
```

It sweeps, shortlists, expands, and enriches in parallel agents and
returns `finalists` ready for the board — surface its `log()` lines as
they arrive, then pick up at step 8. `sources` keeps only programs the
user can fund (the `balances.programs` keys; omit to keep all);
`avoidDestinations` takes the plan's `avoid_final_destinations`;
`avoidTransit` takes the preference of the same name; leave `vibe` out
to skip enrichment. A single origin–destination ask, or a session
without the Workflow tool, runs steps 4–7 by hand instead.

4. Sweep broad, one call per destination bucket, saved to the scratchpad.
   The window, cabin, and regions come from the plan file. A real sweep:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" search \
     --origin SFO --dest LIS,BCN,ATH \
     --start 2026-09-08 --end 2026-10-08 \
     --cabin business --take 1000 --order lowest_mileage > sweep.jsonl
   ```

   ```
   quota remaining: 998
   ```

   For a continent with no pseudo-code (Africa), sweep per program
   instead, and only the programs where the user holds a balance:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" availability \
     --source aeroplan --cabin business --dest-region Africa \
     --take 1000 > africa.jsonl
   ```

   Buckets and per-program sweeps are independent: by hand, spawn one
   subagent per sweep — each makes exactly the one call it would make
   anyway and returns `{file, rows, quota}`.

5. Filter offline with the [jq recipes](#jq-recipes) against the saved
   files. Re-filtering is free; never spend an API call to re-ask a
   question the scratchpad already answers.
6. Expand each finalist with `trip <availability-ID>` (the row's `.ID`).
   The real numbers live there; see
   [Trip detail](#trip-detail-the-bookable-truth). Finalists expand in
   parallel — one subagent per `trip` call.
7. Enrich when the ask has a vibe ("warm", "beachy"): `WebSearch` for
   seasonal weather, visa rules, and destination color. The API knows
   seats, not sunshine. One `WebSearch` subagent per shortlisted
   destination; enrichment spends zero API quota.
8. Present the shortlist as a [cc-present board](#presenting-options) and
   iterate rounds until the user submits. Log each round's outcome in the
   plan's `decisions`.
9. Bridge gaps with [cash positioning flights](#positioning-flights) when
   the award departs somewhere other than the user's home airport.
10. Deliver the final plan: per leg, the program, integer miles and exact
    taxes from `/trips/{id}` (never the search strings), remaining seats,
    the booking link, and the row's `UpdatedAt` — cached snapshots run
    hours to days old, so always surface freshness.

## Trip detail: the bookable truth

`/search` rows are cached teasers; `/trips/{id}` is what the user books.
Field types shift between the two — in trip data, `MileageCost` is an
integer (`44000`) where search's `JMileageCost` is a string (`"44000"`),
and `TotalTaxes` is integer minor units with a `TaxesCurrency` (`18560` +
`"USD"` = $185.60). Booking links sit at the top level of the envelope,
not inside each trip. A real expansion:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" trip <availability-ID> |
  jq '{booking: [.booking_links[] | {label, primary}],
       carriers,
       best: (.data | map(select(.Cabin == "business")) | min_by(.MileageCost)
              | {MileageCost, TotalTaxes, TaxesCurrency, RemainingSeats,
                 FlightNumbers,
                 segments: [.AvailabilitySegments[]
                   | "\(.FlightNumber) \(.OriginAirport)-\(.DestinationAirport) \(.Cabin) (\(.AircraftName))"]})}'
```

```json
{
  "booking": [
    { "label": "Book via Air France/KLM Flying Blue", "primary": true },
    { "label": "Book via Delta SkyMiles", "primary": false },
    { "label": "Book via Virgin Atlantic", "primary": false }
  ],
  "carriers": { "AF": "Air France", "DL": "Delta", "KL": "KLM", "VS": "Virgin Atlantic" },
  "best": {
    "MileageCost": 192500,
    "TotalTaxes": 36550,
    "TaxesCurrency": "USD",
    "RemainingSeats": 1,
    "FlightNumbers": "DL1862, AF23, AF1832",
    "segments": [
      "DL1862 SFO-LAX business (Boeing 737-800)",
      "AF23 LAX-CDG business (Boeing 777-300ER)",
      "AF1832 CDG-ATH business (Airbus A319)"
    ]
  }
}
```

One `.data[]` array carries every cabin's trips for the row — filter on
`.Cabin` before `min_by`, or an economy trip wins the sort.

## Region pseudo-codes

`/search` accepts the seats.aero UI's region pseudo-codes in `--origin` and
`--dest` and expands them server-side (verified live, 2026-07-10). The
expansion is a superset of the UI-documented list — trust what the API
returns over the published table:

| Code | Observed expansion |
|---|---|
| `WST` (US west coast) | SFO, SEA, LAX, YVR, LAS, PHX, PDX, SAN, SLC — the UI documents 8 of these and omits LAX, SEA, PHX |
| `ASA` (Asia) | NRT, ICN, HND, TPE, PVG, HKG, BKK, SIN |

The full code list lives in
[docs/seats-aero-api.md](../../docs/seats-aero-api.md).

> **Warning:** No Africa pseudo-code exists. An Africa-wide sweep is
> per-program `availability --dest-region Africa`, one call per source —
> sweep only the programs where the user holds a balance. The API's Africa
> bucket also includes Indian Ocean (MRU, MLE) and Canary Islands
> (FUE, ACE) airports; drop them when the user means the continent.

Trip memory and planner write-backs store explicit IATA codes, so they
stay valid if the API's expansion shifts. Airport preferences the user
states as pseudo-codes (`WST`, `QBA`) are stored verbatim — `search`
re-expands them server-side on every call.

## Quota discipline

Every API subcommand records the `X-RateLimit-Remaining` response header,
prints `quota remaining: N` on stderr, and caches it; `quota` reads the
cache with zero API calls. The budget is 1,000 calls per day, resetting at
midnight UTC.

- One call per region bucket in the broad sweep — batch destinations into
  comma lists instead of calling per airport.
- Big `--take` beats `--pages`: each page is a separate call.
- Re-filter saved JSONL for every follow-up question; a new call needs a
  new question the scratchpad cannot answer.
- Fan-out never adds calls: batch into comma lists first, then
  parallelize the calls that remain ([Orchestration](#orchestration)).
- The quota cache is last-writer-wins. After a parallel burst, trust
  the minimum the subagents reported, or run `quota` once the burst
  settles — never mid-burst.
- Tell the user when the remaining quota drops below about 100, and
  stop fanning out.

## jq recipes

The sweep recipes ran against live sweeps. The type quirk that bites:
cached `/search` rows carry mileage as strings, so `tonumber` before any
numeric compare.

Keep available business seats under a mileage ceiling:

```bash
jq -c 'select(.JAvailable and (.JMileageCost|tonumber) <= 90000)' sweep.jsonl
```

Hard-drop the plan's avoided final destinations. This filters only rows
*ending* at those airports; the same airports stay valid as connections
and positioning stops. On a real WST-to-Asia sweep with the ICN, GMP,
NRT, and HND list, this cut 50 rows to 20:

```bash
jq -c --argjson avoid "$("${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" plan-show | jq '.avoid_final_destinations')" \
  'select(.Route.DestinationAirport as $d | ($avoid | index($d)) | not)' sweep.jsonl
```

Drop trips connecting through an `avoid_transit` airport. `/search` rows
hide connections, so this runs on `/trips/{id}` output — a connection is
any segment origin after the first:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" trip <availability-ID> |
  jq --argjson transit "$("${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs | jq '.avoid_transit')" \
    '.data |= map(select(any(.AvailabilitySegments[1:][]; .OriginAirport as $o | $transit | index($o)) | not))'
```

Soft-demote an avoided airline — sink its rows to the bottom without
dropping them. Airline fields like `JAirlines` hold comma-joined IATA
codes (`"AF, DL"`), not names, so match `avoid_airlines[].code` and never
a name substring:

```bash
jq -s 'sort_by(.JAirlines | split(", ") | any(. == "ET"))' sweep.jsonl
```

Project a scannable table for eyeballing a sweep:

```bash
jq -r '[.Date,.Route.OriginAirport,.Route.DestinationAirport,.Source,
        .JMileageCost,.JRemainingSeats,.JAirlines,.JDirect]|@tsv' sweep.jsonl | head -5
```

```
2026-10-06	SFO	ATH	flyingblue	192500	1	AF, DL	false
2026-10-05	SFO	ATH	flyingblue	177000	9	AF, KL	false
2026-09-29	SFO	ATH	flyingblue	177000	5	AF, KL	false
2026-09-14	SFO	ATH	flyingblue	177000	6	AF	false
2026-10-06	SFO	ATH	american	57500	0	BA, IB	false
```

## Presenting options

Invoke the `Skill` tool with `cc-present:present` to put the shortlist in
front of the user as a live approval board. The plugin ships the `getaway`
block pack, so the dotted block types below are installed wherever the
plugin is; every field is documented in
[the pack reference](../../.claude/components/reference/blocks.md).

- One `getaway.option-picker` for the shortlist — one entry per finalist,
  `optionId` set to the row's availability `ID`. A tap submits
  `{"optionId": …}`: that finalist is the pick.
- One `getaway.itinerary` per expanded finalist, fed only from
  `/trips/{id}`: integer miles, minor-unit taxes plus currency, remaining
  seats, the primary booking link, the row's `UpdatedAt`, and the segments
  in `Order`.
- A `getaway.flight` for each positioning leg — convert the `fli` price to
  minor units (`305.0` USD becomes `{"amount": 30500, "currency": "USD"}`).
- A `getaway.availability` grid when the user asks about other dates or
  cabins — build it from the saved sweep JSONL, no new API calls. A tap
  submits `{"date": …, "cabin": …}`: expand that cell with `trip`.
- Built-ins carry the rest: a `choice` block for pivots (shift the window,
  swap the region), an `input` block for free-form constraints ("aisle
  seats", "no red-eyes"), a `progress` block while `trip` expansions run
  as parallel subagents — update it as each returns, then swap in the
  finished `getaway.itinerary` blocks.
- Pack interactions arrive as
  `{"type": "pack.interaction", "blockId": …, "payload": …}` with the
  payloads above.
- Iterate rounds — redraft rejected options, add fresh ones — until the
  user submits.

`AskUserQuestion` stays the lightweight path: up to 4 quick questions,
batched in one call, when a full board is overkill.

## Positioning flights

When the award departs from an airport the user is not at, price the cash
leg with the `fli` CLI. Several gap legs price in parallel — one subagent
per `fli` call; a single leg runs inline:

```bash
uvx --from "flights[mcp]" fli flights SFO YVR 2026-09-08 --class BUSINESS --format json |
  jq '{count, cheapest: (.flights | min_by(.price)
       | {price, currency, stops, airline: .legs[0].airline.code, flight: .legs[0].flight_number})}'
```

```json
{
  "count": 103,
  "cheapest": {
    "price": 305.0,
    "currency": "USD",
    "stops": 0,
    "airline": "UA",
    "flight": "2259"
  }
}
```

`--format json` returns a wrapper object, not a bare array: the flight
list is `.flights` and the total is `.count`. The same call doubles as a
cash-versus-points sanity check — when the cash fare undercuts the award's
taxes plus a fair cent-per-point value of the miles, say so.

When the positioning date is flexible, scan a window with `fli dates` and
let the cheapest day anchor the plan:

```bash
uvx --from "flights[mcp]" fli dates SFO YVR --from 2026-09-05 --to 2026-09-12 --class BUSINESS --format json |
  jq '{count, cheapest_days: (.dates | sort_by(.price) | .[0:3]
       | map({date: .departure_date, price, currency}))}'
```

```json
{
  "count": 8,
  "cheapest_days": [
    { "date": "2026-09-12", "price": 266.0, "currency": "USD" },
    { "date": "2026-09-06", "price": 305.0, "currency": "USD" },
    { "date": "2026-09-07", "price": 305.0, "currency": "USD" }
  ]
}
```

`fli dates` also wraps its results: the per-day fares are `.dates`, each a
`{departure_date, price, currency}` with no airline detail. It scans
one-way by default; add `--round` for a round trip.

## Learnings

Durable takeaways from a session have three homes. Always-true facts the
user stated or corrected land in preferences via `prefs-set` — the
`learnings` array (`{date, note}` objects) alongside the keys they
refine. Trip-scoped statements land in the active plan via `plan-set` —
its fields or the `decisions` log. Skill or API corrections land in a
repo doc edit, or append to `~/.getaway/learnings.md` when discovered
outside this repo. The plugin's Stop hook (`hooks/reflect.py`) drives
that reflection at the end of each session. Follow its prompt when it
fires.
