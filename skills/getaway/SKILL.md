---
name: getaway
description: Plans award flights using the seats.aero Partner API. Triggers when the user wants to plan an award flight or trip on points or miles, find award availability or saver space between airports or across a region ("west coast to Asia", "somewhere warm in September"), compare mileage programs for a route, pull booking links or taxes for an award, find a cash positioning flight, or get creative with routings — a lie-flat award to a hub like NRT with a cash hop onward, an open jaw, or two awards stitched across programs — or mentions seats.aero. Needs a seats.aero Pro API key, from SEATS_AERO_API_KEY or a 1Password reference in ~/.getaway/preferences.json.
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
{"op_ref":null,"home_airport":"SFO","origin_airports":["SFO","SJC","SAN","PDX","DEN","LAS","SLC","YVR"],"avoid_transit":[],"avoid_airlines":[{"code":"ET","name":"Ethiopian Airlines","strength":"soft"}],"statuses":{},"balances":{"programs":{},"transferable":{}},"documents":{"passports":[],"residency":[],"visas":[]},"learnings":[]}
```

The keys that steer planning:

| Key | Meaning |
|---|---|
| `origin_airports` | IATA or region pseudo-codes stored verbatim, `home_airport` likewise; the default origin set |
| `avoid_transit` | Explicit IATA only — pseudo-codes expand at save; hard drop on connections, enforced against `/trips` segments |
| `avoid_airlines` | `{code, name, strength}` objects; `soft` demotes, `hard` drops, and matching keys on `code` |
| `statuses` | Program slug to elite tier, verbatim (`{"united": "1K"}`); ties on mileage cost break toward these carriers |
| `balances.programs`, `balances.transferable` | Program slug to points; bank currencies |
| `documents` | Free-text `passports`, `residency`, and `visas` arrays; personalize the Enrich visa notes and the transit/entry flags — all empty means US-passport phrasing and no Transit pass |
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
| `availability --source <program> [flags]` | 1 per page | Per-program bulk dump via `/availability`; the route for region-level origin filtering, and the continent-sweep fallback when no [pseudo-code](#region-pseudo-codes) fits |
| `routes --source <program> [flags]` | 1 | A program's whole monitored route map via `/routes` — the [gateway-set](#gateway-sets) refinement input |
| `trip <availability-ID>` | 1 | One row's bookable trips via `/trips/{id}`: segments, exact taxes, booking links |
| `quota` | 0 | Print the last recorded quota from cache; exits 4 before the first API call |

`search` flags: `--origin`/`--dest` (required, comma lists), `--start`/
`--end` (`YYYY-MM-DD`), `--cabin`, `--sources`, `--carriers`, `--direct`,
`--order lowest_mileage`, `--take` (default 500), `--pages` (default 1).

`availability` flags: `--source` (required, one program slug), `--cabin`,
`--start`/`--end`, `--origin-region`/`--dest-region` (full continent names:
Africa, Asia, Europe, North America, Oceania, South America), `--take`,
`--pages`.

`routes` flags: `--source` (required, one program slug),
`--origin-region`/`--dest-region` (same continent names, filtered
client-side by jq — `/routes` has no server region param).

`search`, `availability`, and `routes` emit JSONL — one object per line,
deduped by `.ID` across pages — so output pipes straight into `jq` or a
scratchpad file. `--pages` walks the API's `cursor` + `skip` continuation,
and every page costs one quota call: prefer one page with a big `--take`.
`routes` takes no pages: one call returns the program's entire route map,
thousands of lines — always redirect to a scratchpad file.
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
   The script holds the six-phase pipeline — sweep, shortlist,
   onward, bridge, expand, enrich — and its intermediate results; the
   conversation holds only the finalists and hybrids.
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
  gatherers running only post-prime `abwc-seed` seeding.)
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
no points balances — and without balances, ranking bias and top-up math
are guesswork; balances never gate a search. Offer the `getaway:onboard`
skill ([skills/onboard/SKILL.md](../onboard/SKILL.md)) before planning —
skippable; a decline means planning proceeds on the current defaults.
If no file exists yet, run `prefs-init` on the skip path so `prefs` has
defaults to read. A `PostToolUse` hook (`hooks/onboard.py`, sibling to
`reflect.py`) backstops the offer once per session and never blocks.

1. Read the globals with `prefs`: the origin set, `avoid_transit`,
   `avoid_airlines`, `documents`, and which programs hold points. Balances bias
   ordering and feed the
   [affordability annotations](#affordability-and-top-ups); a zero or
   missing balance never removes a program from a sweep or a shortlist.
   When mileage costs tie, prefer carriers where `statuses` shows the
   user holds elite status. On a business-cabin ask, the hard product
   joins the ranking — see [Seat quality](#seat-quality). Preferences carry nothing trip-shaped — no window, cabin, or
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

Steps 4–8 are the fan-out core. When the ask spans two or more
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
    buckets: [{name: "iberia", dests: ["LIS", "BCN", "ATH"]},
              {name: "africa", dests: ["QAF"]}],
    avoidDestinations: ["ICN", "GMP"], avoidTransit: ["IST"],
    avoidAirlines: [{code: "ET", strength: "soft"}],
    documents: {passports: ["Canada"], residency: ["US green card"], visas: []},
    mileageCeiling: 90000, travelers: 2, maxFinalists: 6,
    vibe: "warm",
    hybrid: {gateways: ["LIS", "MAD", "LHR", "CDG"],
             onwardDests: ["ATH", "CMN"],
             cashCutoffMinutes: 240, maxHybrids: 3}
  }
})
```

It sweeps, shortlists, prices onward legs, expands, and enriches in
parallel agents and returns `finalists` plus `hybrids` ready for the
board — surface its `log()` lines as they arrive, then pick up at
step 9. On a business ask it expands
roughly 1.5× `maxFinalists` candidates, classifies each against
[seat-quality.md](seat-quality.md), resolves mixed fleets by
`WebSearch`, and re-ranks before truncating — the buffer's cost is
covered in [Quota discipline](#quota-discipline). `sources` cuts the shortlist to
the named programs — an offline filter after the sweep, not fewer API
calls — and only when the user explicitly asks ("only search united");
never derive it from balances. `programSweeps`
(`{source, destRegion}` each) runs the per-program fallback sweeps
described in step 4.
`avoidDestinations` takes the plan's `avoid_final_destinations`;
`avoidTransit` takes the preference of the same name; leave `vibe` out
to skip enrichment. `documents` takes the preference of the same name:
omitted or all-empty, visa notes keep the US-passport phrasing and the
Transit pass is skipped; with documents on file, finalists and hybrids
routed through a flagged point return a `transit` array of
`{airport, risk, transitNote}` flags for the board. `hybrid` rides every region- or vibe-scale ask:
`gateways` (required, non-empty, concrete IATA — never pseudo-codes)
is the [gateway set](#gateway-sets); `onwardDests` (optional IATA)
defaults to the direct shortlist's distinct destinations, top 4;
`cashCutoffMinutes` defaults to 240
([The cash-cabin default](#the-cash-cabin-default)); `maxHybrids`
defaults to 3, capped at 4. An `onwardDests` airport on
`avoidDestinations` throws — an onward destination *is* a final
destination; gateways are waypoints and exempt. Omitting `hybrid`
skips every hybrid phase — direct awards only. A single
origin–destination ask, or a session without the Workflow tool, runs
steps 4–8 by hand instead.

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

   Africa is a bucket like any other: sweep it with `--dest QAF`, an
   undocumented pseudo-code
   ([Region pseudo-codes](#region-pseudo-codes)). The per-program
   fallback below handles what `search` cannot — region-level origin
   filtering, or a day `QAF` misbehaves (empty QAF results mean a fresh
   pass, by hand or a second Workflow call with `programSweeps`). When
   assembling that list, include all programs with funded ones first,
   so a pass capped by [quota discipline](#quota-discipline) spends its
   calls on the likeliest-bookable options. When quota cuts a sweep
   short, say which programs went unswept:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" availability \
     --source aeroplan --cabin business --dest-region Africa \
     --take 1000 > africa.jsonl
   ```

   Hybrids add exactly one more call: a single `search` from the
   origins to the whole comma-listed [gateway set](#gateway-sets),
   saved as its own gateway sweep file.

   Buckets, the gateway sweep, and per-program sweeps are independent:
   by hand, spawn one subagent per sweep — each makes exactly the one
   call it would make anyway and returns `{file, rows, quota}`.

5. Filter offline with the [jq recipes](#jq-recipes) against the saved
   files. Re-filtering is free; never spend an API call to re-ask a
   question the scratchpad already answers. Gateway rows shortlist
   separately — the same recipes minus the avoid-destinations drop (a
   gateway is a waypoint, not an endpoint), deduped to each gateway's
   best row.
6. Expand each finalist with `trip <availability-ID>` (the row's `.ID`).
   The real numbers live there; see
   [Trip detail](#trip-detail-the-bookable-truth). Finalists expand in
   parallel — one subagent per `trip` call. On a business plan,
   classify each trip's longest business segment against
   [seat-quality.md](seat-quality.md) while expanding; `barely`
   products sink per [Seat quality](#seat-quality).
7. Compose routings ([Routing strategies](#routing-strategies)). Price
   each shortlisted gateway's onward cash leg with `fli` at the cabin
   [the cash-cabin default](#the-cash-cabin-default) picks — legs price
   in parallel, one subagent per call, zero API quota
   ([Cash positioning](#cash-positioning) has the calls). One `search`
   from the top gateways to the onward destinations covers two-award
   stitches across all programs. Assemble the hybrids and rank them
   beside the directs on total cost — miles plus taxes plus cash. A
   positioning gap — the award departs somewhere other than the user's
   home airport — prices the same way and joins the same total.
8. Enrich when the ask has a vibe ("warm", "beachy"): `WebSearch` for
   seasonal weather, visa rules, and destination color. The API knows
   seats, not sunshine. One `WebSearch` subagent per shortlisted
   destination; enrichment spends zero API quota. Visa notes address
   the traveler `documents` describes — all arrays empty keeps the
   US-passport phrasing. Verify-marked seat
   products resolve here too — one `WebSearch` per mixed-fleet
   finalist, vibe or no vibe. With documents on file, the Transit pass
   runs the same way by hand: one `WebSearch` subagent per unique
   connection airport and per hybrid gateway, flagging the risky ones —
   a flag never drops a routing.
9. Present the shortlist as a [cc-present board](#presenting-options) —
   directs and hybrids together, each with its total-cost line, plus a
   "Transit check" section when any option carries transit flags — and
   iterate rounds until the user submits. Log each round's outcome in
   the plan's `decisions`.
10. Deliver the final plan: per leg, the program, integer miles and exact
    taxes from `/trips/{id}` (never the search strings), remaining seats,
    the seat product and verdict on a business leg
    ([Seat quality](#seat-quality)), the booking link, the row's `UpdatedAt` — cached snapshots run hours
    to days old, so always surface freshness — and the leg's
    [affordability line](#affordability-and-top-ups): covered, a
    transfer suggestion, or a buy estimate citing the rate's source and
    date. A hybrid delivers every component: each award booking on its
    own lines, each cash leg with airline, flight number, and fare, and
    the summed total-cost line the board showed.

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
       best: ((.data | map(select(.Cabin == "business")) | min_by(.MileageCost)) as $t
              | if $t == null then null else $t
              | {MileageCost, TotalTaxes, TaxesCurrency, RemainingSeats,
                 FlightNumbers,
                 segments: [.AvailabilitySegments[]
                   | "\(.FlightNumber) \(.OriginAirport)-\(.DestinationAirport) \(.Cabin) (\(.AircraftName))"]} end)}'
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
`.Cabin` before `min_by`, or an economy trip wins the sort. The same
discipline runs per segment: the [seat-quality](#seat-quality) verdict
rates the longest *business* segment, never the longest segment —
connectors on a business award can ride economy.

## Affordability and top-ups

Balances bias and annotate; they never exclude. An unfunded finalist
keeps its shortlist spot and gains an annotation — dropping it hides an
option one transfer could fund.

Shortfall per finalist: trip `MileageCost` (the integer from
`/trips/{id}`, never search's string form) × travelers, minus the
funding pool — the program balance plus what bank partners can move
there, each credited at its own ratio;
[transfer-partners.md](transfer-partners.md) maps each bank's programs
and ratios.

Transfer first. When a transfer covers the gap, name the bank, the
amount, and the ratio ("60k of the 80k Chase balance to united at 1:1")
before any cash option. A small residual shortfall — a judgment call,
no numeric threshold: small relative to the award's total cost, and
only when buying is plausibly good value — earns a `WebSearch` for the
program's current buy-points rate and any active sale or bonus;
present "buy N points ≈ $X" beside the taxes, citing the rate's source
and date. And when the top-up cost plus taxes approaches the cash
fare, say so — the same cash-versus-points check as
[Routing strategies](#routing-strategies).

Ranking stays mileage-first. When finalists land within roughly 10–15%
of each other, prefer the one the user can already fund; the rest keep
their rank and their annotation — covered, a transfer suggestion, or a
buy estimate.

## Seat quality

Business class spans everything from enclosed suites to seats that
barely earn the cabin name. The verdict table lives in
[seat-quality.md](seat-quality.md) — carrier + aircraft to `suite`,
`solid`, `dated`, or `barely`, with a Verify mark on mixed mid-retrofit
fleets. The verdict rates the longest business-cabin segment; segments
carry their own `.Cabin`, so filter before taking the longest — a
narrowbody positioning leg never drags down a trip.

`barely` soft-demotes, the same mechanic as a soft `avoid_airlines`
entry: the finalist sinks below every true lie-flat regardless of
mileage, keeps its spot, and carries an explicit warning. The avoid
list outranks the seat — a soft-avoided airline sinks harder than a
`barely` product, so the sort runs (soft, `barely`, mileage). Everything
else stays mileage-first, and an unknown or unclassified product ranks
neutral — never demote what the table doesn't condemn. Within the
[affordability](#affordability-and-top-ups) near-tie band, a better
product breaks the tie the same way funding does.

A Verify mark means the fleet is mixed — BA's 777s fly both old Club
World and Club Suite. Resolve the specific flight with a `WebSearch`
(the carrier's seat map for that flight number and date, recent cabin
reviews) during enrichment; zero quota. Every business finalist gets a
product note in presentation; a `barely` verdict reads as a warning,
not a footnote.

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

> **Warning:** `QAF` (Africa) works on `/search` yet sits in no
> UI-documented list — verified live 2026-07-12; its observed expansion
> (CMN, CAI, ADD, CPT, JNB, NBO) is a floor like the table above. The
> per-program `availability --dest-region Africa` fallback sweeps all
> programs, ordered funded-first, never trimmed to the funded ones —
> and that Africa bucket also includes Indian Ocean (MRU, MLE) and
> Canary Islands (FUE, ACE) airports; drop them when the user means the
> continent.

Trip memory and planner write-backs store explicit IATA codes, so they
stay valid if the API's expansion shifts. Airport preferences the user
states as pseudo-codes (`WST`, `QBA`) split by consumer: `home_airport`
and `origin_airports` store them verbatim — `search` re-expands them
server-side on every call — while `avoid_transit` expands them to
member airports at save, since transit enforcement is a literal match
against `/trips/{id}` segment codes.

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
- A business plan expands a buffer — roughly 1.5× `maxFinalists` trip
  calls, 9 instead of 6 at the defaults — so the
  [seat-quality](#seat-quality) re-rank sorts real products before
  truncating. That is the acknowledged price of never ranking a
  yin-yang seat over a true flat bed; the low-quota path drops the
  buffer first.
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

Re-rank expanded finalists by seat quality — the workflow's exact sort,
manual-path form. `product` comes from classifying each trip against
[seat-quality.md](seat-quality.md); a missing `product` sorts neutral:

```bash
jq -s 'sort_by(.soft, .product == "barely", .mileage)' finalists.jsonl
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
  hybrids included, `optionId` set to the award row's availability `ID`.
  A tap submits `{"optionId": …}`: that finalist is the pick.
- A built-in `section` block ("Total cost", `md` body) beside the
  picker — one line per finalist: miles + taxes + cash onward (zero cash
  on a pure award), the affordability note — covered, a transfer
  suggestion, or a buy estimate
  ([Affordability and top-ups](#affordability-and-top-ups)) — and a seat
  line on business finalists — the product and its
  [verdict](#seat-quality), a `barely` phrased as a warning ("old Club
  World — barely business, ranked below every true flat bed"). Pack
  schemas are closed; totals, cash components, affordability, and seat
  verdicts ride the `md` body, never extra fields on a pack block.
- A "Transit check" `section` block whenever any finalist or hybrid
  carries transit flags — one line per flagged option: the airport,
  transit versus entry, the risk, and what to verify ("MAD entry —
  possible: self-transfer means clearing Schengen immigration; confirm
  visa-free entry for a Canadian passport"). A flag never pulls an
  option off the board.
- One `getaway.itinerary` per expanded finalist, fed only from
  `/trips/{id}`: integer miles, minor-unit taxes plus currency, remaining
  seats, the primary booking link, the row's `UpdatedAt`, and the segments
  in `Order`. A hybrid's detail card is one `getaway.itinerary` per award
  booking — two for a stitch — plus its cash-leg `getaway.flight`s.
- A `getaway.flight` with `price` for each cash leg, positioning or
  gateway-onward — convert the `fli` price to minor units (`305.0` USD
  becomes `{"amount": 30500, "currency": "USD"}`).
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

## Routing strategies

A trip is a composition of legs, not one availability row. Every
region- or vibe-scale plan generates hybrid routings alongside the
direct awards, and they all compete on the same axis: total cost —
miles plus taxes plus cash. Any legal composition of legs is fair
game — invent the shape, then price it. A self-transfer gateway on
separate tickets is an entry point, not a connection — the Transit
pass flags its entry requirements for the traveler's `documents`.

The levers:

- Direct award — one availability row; the baseline every hybrid must
  beat.
- Cash positioning — a cash hop to the award's origin
  ([Cash positioning](#cash-positioning)).
- Gateway hybrid — a lie-flat award to a hub, a cash ticket onward
  ([Gateway hybrids](#gateway-hybrids)).
- Open jaw — in through one airport, home from another. The return is
  a second Workflow invocation: origins are the chosen endpoints plus
  gateways, dests the home set.
- Two-award stitch — a second award onward from the gateway, any
  program; the onward `search` sweeps them all in one call.
- Long-range positioning — a cheap long cash leg to a region rich in
  award space, then the award from there.
- Top-ups — transfer or buy the missing miles
  ([Affordability and top-ups](#affordability-and-top-ups)).
- Pure cash — every `fli` quote doubles as a cash-versus-points sanity
  check: when the cash fare undercuts the award's taxes plus a fair
  cent-per-point value of the miles, say so.

### The cash-cabin default

Cash legs at or under 240 minutes book economy; longer legs book
business. The same cutoff picks the cabin for a stitched onward award.
A per-trip override ("business everywhere") logs in the plan's
`decisions` and feeds the Workflow's `hybrid.cashCutoffMinutes`; a
durable one routes through [Learnings](#learnings).

### Gateway sets

Gateways are concrete IATA codes, never pseudo-codes — `fli` and jq
matching need real airports. Observed pseudo-code expansions seed the
sets: Asia is `ASA`'s NRT, ICN, HND, TPE, PVG, HKG, BKK, SIN
([Region pseudo-codes](#region-pseudo-codes)); the documented region
rows in [docs/seats-aero-api.md](../../docs/seats-aero-api.md) floor
the rest. Refine per program with `routes` — rank a region's airports
by monitored-route count and keep the top hubs:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" routes \
  --source aeroplan --dest-region Asia > routes.jsonl
jq -rs 'group_by(.DestinationAirport)
        | map({hub: .[0].DestinationAirport, n: length})
        | sort_by(-.n) | .[0:8][] | "\(.hub)\t\(.n)"' routes.jsonl
```

One `routes` call returns the program's entire monitored map — 8,260
rows for aeroplan (observed 2026-07-12) — so always redirect to a
scratchpad file. Each row's `Distance` is a free great-circle proxy
for [the cash-cabin default](#the-cash-cabin-default) before any
`/trips` call.

### Gateway hybrids

The worked shape: the direct award to the real destination is scarce
or pricey, but a major gateway has lie-flat space — so book the award
to the gateway and a separate cash ticket onward:

- Award: SFO–NRT business, 88,000 aeroplan miles + $118 taxes
- Onward: NRT–TPE cash, economy per
  [the cash-cabin default](#the-cash-cabin-default), $96 via `fli`
- Total: 88k + $118 taxes + $96 cash — one line beside every
  single-ticket finalist, competing on total cost.

> **Warning:** `avoid_final_destinations` never vetoes a gateway. NRT
> on the avoid list means no trip *ends* at NRT — it stays fully valid
> as the hub the award lands at and the cash leg leaves from. The veto
> bites on the onward destination instead: the Workflow throws when
> `hybrid.onwardDests` intersects `avoidDestinations`.

### Cash positioning

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
list is `.flights` and the total is `.count`.

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
