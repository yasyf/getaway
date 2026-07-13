# Planning

How each planning move works, from the raw ask to the presented board. Command flags live in `--help`; the rulings behind every rule here live in [doctrine.md](doctrine.md).

## Parsing the ask

The canonical ask, from the founding transcript:

> "I want to go away for roughly a week... warm, beachy, cheap points tickets for business class... avoid the common places we always go like seoul or tokyo"

(The full transcript adds "leaving in the next couple days" and "outside north america".) Every clause pins a key; parse before asking anything:

| Clause | Where it lands |
|---|---|
| "roughly a week" | `window.trip_length_days: 7`; a round trip is implied, so `plan.round_trip: true` and `return_viability` activates |
| "leaving in the next couple days" | `window.start` a day or two out; a tight window widens the destination set, never the date set |
| "outside north america" | `regions.exclude: ["North America"]` |
| "warm, beachy" | `vibe: ["warm", "beachy"]` — activates `destination_context`; weather in the window becomes evidence, never a filter |
| "cheap points tickets for business class" | `cabin: "business"`; the derived profile puts `affordability` and `seat_quality` at `primary`, and `cash_anomaly` auto-activates on business |
| "avoid the common places we always go like seoul or tokyo" | "always" is durable-shaped: `avoid_destinations` in prefs via `prefs set`, unioned with the trip's `avoid_final_destinations` — ICN, GMP, NRT, HND, endpoints only; NRT stays a legal gateway |

`trip profile` derives exactly this posture from the pinned keys, so the dense sentence needs zero clarifying questions about emphasis. Ask only what no clause pins — here, party size. Durable-versus-trip is the one split to keep sharp: "we always go" and "I never fly X" go to prefs; "not this trip" goes to the trip doc.

## Origin expansion

Start at the gateway region, not the airport. `WST` covers the US west coast origin set in one `/search` operand — `registry regions` carries the code with its observed expansion, a superset of the UI-documented list. Pin `plan.origins: ["WST"]` and the server re-expands it on every call, so the set stays current without maintenance.

Widen outward only when the region comes back thin: add positioning origins one cheap cash hop away, as explicit IATA codes beside the region code. The cash leg to reach them prices later, in the bridge phase — origin expansion decides where awards may start, not what they cost.

## Region-broad sweeps

When the destination is a region or a vibe, sweep pseudo-codes on `/search` first. `QAF` leads every Africa ask: it works on `/search` despite sitting in no UI-documented list, and its observed expansion (CMN, CAI, ADD, CPT, JNB, NBO) is a floor, not a ceiling. `ASA` covers Asia's large airports, `EUR` Europe's; `registry regions` carries every code with observed expansions — never hand-maintain a list.

When no pseudo-code fits, fall back to per-program `plan.program_sweeps` entries (`availability --dest-region <Continent>` under the hood), ordered funded-programs-first and never trimmed to the funded ones: biases order, never gate. One caution: the API's Africa region includes Indian Ocean (MRU, MLE) and Canary Islands (FUE, ACE) rows; drop them when the user means the continent.

## Season awareness

Sanity-check each bucket's season inside the window before sweeping it. "Warm, beachy" in September rules out southern-hemisphere winter coasts and rules in the shoulder-season Mediterranean; monsoon calendars split Southeast Asia coast by coast. Shoulder season is usually what the ask wants — award space opens exactly when the crowds leave. Events cut both ways: a festival makes a destination or a layover city more interesting, a city-wide congress blows up the hotel math; both are `destination_context` evidence. Season judgment picks which buckets to sweep — it never suppresses a row after the fact. A bucket that came back cheap in the "wrong" season is a finding to present.

## Affordability and top-ups

`afford --program <slug> --miles <n>` computes the funding position: the program balance, transfer paths in from bank currencies at their ratios, and the shortfall. `--include-purchase` prices buying the shortfall at the program's published rate (`registry points-pricing` has rate, typical sale, and cadence per program).

Transfer first. When a transfer covers the gap, name the bank, the amount, and the ratio ("60k of the 80k Chase balance to united at 1:1") before any cash option. Price a purchase only for a small residual on an award already worth it, and present "buy N points for about $X" beside the taxes, citing the rate's source. All of it annotates: an unfunded finalist keeps its spot with its top-up path attached. When top-up cost plus taxes approaches the cash fare, say so on the board.

## Round trips

Returns are a second Workflow invocation, dispatched after the user picks or narrows the outbound: origins become the chosen endpoints plus gateways, destinations the home set, and artifacts land `return-` prefixed in the same trip. An open jaw is the same second invocation with different endpoints.

On the outbound run, the `return_viability` factor reads only the cache — zero quota — and flags finalists whose return has no observed space ("no J space home inside the window yet"). It never filters an outbound; an unverified return is a warning line, and the second invocation is what verifies it.

## Routing strategies

The founding instruction, verbatim: "lie flat on points to a common airport such as NRT, and then cash... the point is we can get creative with routings". A trip is a composition of legs, not one availability row. Invent the shape, then price it. Every shape competes at presentation on total cost: miles plus taxes plus cash.

- Direct award — one availability row; the baseline every hybrid must beat.
- Gateway hybrid — a lie-flat award to a hub, a cash ticket onward. `plan.hybrid` declares the `gateways` and `onward_dests` sets and `max_hybrids` caps the compositions; `shortlist run --gateway` finds award legs into the gateways and the bridge phase prices cash onward.
- Two-award stitch — a second award onward from the gateway, any program; `shortlist onward <slug>` works the swept onward rows.
- Cash positioning — a cash hop to the award's true origin when it isn't the user's airport.
- Long-range positioning — a cheap long cash leg into an award-rich region, then the award from there.

Gateway sets are concrete IATA codes, never pseudo-codes. Seed them from the expansions in `registry regions`; refine per program with `routes <source>` ranked by monitored-route count: one call returns the program's entire route map, so redirect it to a scratch file. Cash and stitched onward legs book economy at or under 240 minutes and business above, decided per leg; the cutoff is a code constant, not a knob. The avoid union never vetoes a gateway: Tokyo on the avoid list kills NRT as an endpoint, not as the hub the award lands at. `trip set` enforces the flip side, rejecting an onward set that intersects the union.

## Presenting options

Present finalists as a live cc-present board (the `cc-present:present` skill). The plugin ships the `getaway` block pack; every block and field is documented in [.claude/components/reference/blocks.md](../../../.claude/components/reference/blocks.md). Rows come from `trip artifact read <slug> finalists.json`, where each finalist carries a `{factor_id: {verdict, evidence}}` map for every active factor.

- One `getaway.option-picker` for the shortlist, hybrids included, `optionId` set to the availability row's `ID`; a tap streams back the pick.
- One `getaway.itinerary` per expanded finalist, fed only from `expand` output: integer miles, taxes as integer minor units with a currency (`{"amount": 12050, "currency": "USD"}` renders $120.50), remaining seats, the primary entry from `booking_links`, segments in order, and the row's `UpdatedAt` as `updatedAt` — the block renders it as the freshness stamp ("6 hours ago"); flag stale rows in prose and offer a refresh. Never feed a block from raw `/search` rows, whose mileage is a string.
- A `getaway.flight` with `price` for each cash leg, positioning or onward; convert major-unit floats to minor units before composing (305.0 USD becomes `{"amount": 30500, "currency": "USD"}`).
- A `getaway.availability` grid when the user asks about other dates or cabins — built from `cache query`, zero API calls; a tapped cell asks for that date and cabin expanded.
- Beside the picker, one evidence `section` block per finalist: one line per active factor, straight from the finalist's evidence map — the funding position, the seat product (a `barely` verdict phrased as a warning), the layover line answering the user's own question of how interesting the layover city is alongside its duration, the cash-anomaly note, the return flag. A `demote` verdict reads as a warning, not a footnote. Pack schemas are closed: evidence rides `section` blocks, never extra fields on pack blocks.

`AskUserQuestion` stays the lightweight path — at most 4 quick questions in one call — when a full board is overkill.
