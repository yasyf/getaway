---
name: getaway
description: Plans award flights using the seats.aero Partner API. Triggers when the user wants to plan an award flight or trip on points or miles, find award availability or saver space between airports or across a region ("west coast to Asia", "somewhere warm in September"), compare mileage programs for a route, pull booking links or taxes for an award, or find a cash positioning flight — or mentions seats.aero. Needs a seats.aero Pro API key, from SEATS_AERO_API_KEY or a 1Password reference in ~/.getaway/preferences.json.
allowed-tools: Bash(curl:*), Bash(jq:*), Bash(op:*), Bash(uvx:*)
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

On first use, write the preferences file, then confirm its defaults with
the user:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs-init
```

```
/Users/<user>/.getaway/preferences.json
```

`prefs` prints the current file as compact JSON, and these are the shipped
defaults:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs
```

```json
{"version":1,"op_ref":null,"home_airport":"SFO","origin_airports":["SFO","SJC","SAN","PDX","DEN","LAS","SLC","YVR"],"cabin":"business","trip_length_days":7,"departure_days":["Sun","Mon"],"avoid_destinations":["ICN","GMP","NRT","HND"],"avoid_airlines":[{"code":"ET","name":"Ethiopian Airlines","strength":"soft"}],"balances":{"programs":{},"transferable":{}},"learnings":[]}
```

The keys that steer planning:

| Key | Meaning |
|---|---|
| `origin_airports` | Explicit IATA codes; the default origin set |
| `cabin` | Default cabin for every sweep |
| `trip_length_days`, `departure_days` | The default window shape |
| `avoid_destinations` | Hard drop; city codes pre-expanded to airports |
| `avoid_airlines` | `{code, name, strength}` objects; `soft` demotes, `hard` drops, and matching keys on `code` |
| `balances.programs`, `balances.transferable` | Program slug to points; bank currencies |
| `learnings` | Session takeaways; see [Learnings](#learnings) |

## Command reference

| Command | API calls | Purpose |
|---|---|---|
| `prefs-init` | 0 | Write the `~/.getaway/preferences.json` template; exits 3 if the file exists |
| `prefs` | 0 | Print preferences as compact JSON; exits 3 when the file is missing or not `version: 1` |
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
Exit codes: 2 no key, 3 preferences problem, 4 no quota recorded, 64 usage.

## Planning workflow

1. Read preferences with `prefs`. Derive the origin set, cabin, date
   window (`trip_length_days` anchored on `departure_days`), avoid lists,
   and which programs hold points.
2. Pin down the ask with one `AskUserQuestion` call — up to 4 questions
   (window, region, one-way or round trip, travelers), concrete options
   each. Skip anything preferences already answer.
3. Sweep broad, one call per destination bucket, saved to the scratchpad.
   A real sweep:

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

4. Filter offline with the [jq recipes](#jq-recipes) against the saved
   files. Re-filtering is free; never spend an API call to re-ask a
   question the scratchpad already answers.
5. Expand each finalist with `trip <availability-ID>` (the row's `.ID`).
   The real numbers live there; see
   [Trip detail](#trip-detail-the-bookable-truth).
6. Enrich when the ask has a vibe ("warm", "beachy"): `WebSearch` for
   seasonal weather, visa rules, and destination color. The API knows
   seats, not sunshine.
7. Present the shortlist as a [cc-present board](#presenting-options) and
   iterate rounds until the user submits.
8. Bridge gaps with [cash positioning flights](#positioning-flights) when
   the award departs somewhere other than the user's home airport.
9. Deliver the final plan: per leg, the program, integer miles and exact
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

Preferences store explicit IATA codes, never pseudo-codes, so they stay
valid if the API's expansion shifts.

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
- Tell the user when the remaining quota drops below about 100.

## jq recipes

All four ran against live sweeps. The type quirk that bites: cached
`/search` rows carry mileage as strings, so `tonumber` before any numeric
compare.

Keep available business seats under a mileage ceiling:

```bash
jq -c 'select(.JAvailable and (.JMileageCost|tonumber) <= 90000)' sweep.jsonl
```

Hard-drop avoided destinations with the prefs list. On a real WST-to-Asia
sweep this cut 50 rows to 20, removing every ICN, NRT, and HND landing:

```bash
jq -c --argjson avoid '["ICN","GMP","NRT","HND"]' \
  'select(.Route.DestinationAirport as $d | ($avoid | index($d)) | not)' sweep.jsonl
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
front of the user as a live approval board:

- One card per option, titled like `CPT · Aeroplan · 88k + $120 · dep Sun
  9/6`, with an `approval` block inside each card.
- One `choice` block for pivots — shift the window, swap the region.
- One `input` block for free-form constraints ("aisle seats", "no red-eyes").
- A `progress` block while `trip` expansions run in the background.
- Iterate rounds — redraft rejected cards, add fresh options — until the
  user submits.

`AskUserQuestion` stays the lightweight path: up to 4 quick questions,
batched in one call, when a full board is overkill.

## Positioning flights

When the award departs from an airport the user is not at, price the cash
leg with the `fli` CLI:

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

Durable takeaways from a session have two homes: preferences the user
stated or corrected land in the prefs `learnings` array (`{date, note}`
objects) alongside the keys they refine, and skill or API corrections
discovered outside this repo append to `~/.getaway/learnings.md`. The
plugin's Stop hook (`hooks/reflect.py`) drives that reflection at the end
of each session. Follow its prompt when it fires.
