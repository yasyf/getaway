# Planning

How each planning move works, from the raw ask to the presented board. Command flags live in `--help`; the rulings behind every rule here live in [doctrine.md](doctrine.md).

## Parsing the ask

The canonical ask, from the founding transcript:

> "I want to go away for roughly a week... warm, beachy, cheap points tickets for business class... avoid the common places we always go like seoul or tokyo"

(The full transcript adds "leaving in the next couple days" and "outside north america".) Every clause pins a key; parse before asking anything:

| Clause | Where it lands |
|---|---|
| "roughly a week" | a round trip is implied: two `plan.legs` intents, the destination leg plus a `return` with `dests: "$origins"`, with `preferences.trip_length: {value: {days: 7, basis: "elapsed_door_to_door"}, priority: "secondary"}` — and a week away implies a bed, so `plan.lodging` comes in scope |
| "leaving in the next couple days" | `preferences.outbound_departure_window` a day or two out; soft dates sweep the stated range plus seven days of padding, so a near-miss exists in the cache before the model judges it |
| "outside north america" | `regions.exclude: ["North America"]` |
| "warm, beachy" | `vibe: ["warm", "beachy"]` — activates `destination_context`; weather in the window becomes evidence, never a filter |
| "cheap points tickets for business class" | `preferences.cabin: {value: "business", priority: "primary"}` and a `preferences.mileage_target` when the user names a number; the derived profile puts `affordability` and `seat_quality` at `primary`, and `cash_anomaly` auto-activates on business |
| "avoid the common places we always go like seoul or tokyo" | "always" is durable-shaped: `avoid_destinations` in prefs via `prefs set`, unioned with the trip's `avoid_final_destinations` — ICN, GMP, NRT, HND, endpoints only; NRT stays a legal gateway |

Preferences and constraints are different branches, and the CLI rejects the same key in both. A preference is `{value, priority: "primary" | "secondary" | "note"}`, an ordinal lane rather than a numeric weight, and it orders or annotates, never gates. A constraint is hard and belongs there only when the user has explicitly confirmed it ("I *must* be back by the 14th" after you ask). When in doubt, it is a preference: the perfect trip that returns a day late must surface with its miss named, not vanish.

`trip profile` derives exactly this posture from the pinned keys, so the dense sentence needs zero clarifying questions about emphasis. Ask only what no clause pins — here, party size. Durable-versus-trip is the one split to keep sharp, and the CLI enforces it: each write path rejects the other store's keys. "We always go" and "I never fly X" go to prefs; "not this trip" goes to the trip doc.

## Origin expansion

Start at the gateway region, not the airport. `WST` covers the US west coast origin set in one `/search` operand — `registry regions` carries the code with its observed expansion, a superset of the UI-documented list. Pin `plan.legs[0].origins: ["WST"]` and the server re-expands it on every call, so the set stays current without maintenance. A pseudo-code is a sweep operand only: the sweep records the server-expanded airports on the leg, and every feasibility check compares those concrete codes — never the literal `WST`.

Widen outward only when the region comes back thin: add positioning origins one cheap cash hop away, as explicit IATA codes beside the region code. The cash leg to reach them prices later, in the bridge node — origin expansion decides where awards may start, not what they cost.

## Region-broad sweeps

When the destination is a region or a vibe, sweep pseudo-codes on `/search` first. `QAF` leads every Africa ask: it works on `/search` despite sitting in no UI-documented list, and its observed expansion (CMN, CAI, ADD, CPT, JNB, NBO) is a floor, not a ceiling. `ASA` covers Asia's large airports, `EUR` Europe's; `registry regions` carries every code with observed expansions — never hand-maintain a list.

When no pseudo-code fits, fall back to per-program `program_sweeps` entries on the leg (`availability --dest-region <Continent>` under the hood), ordered funded-programs-first and never trimmed to the funded ones: preferences order, never gate. One caution: the API's Africa region includes Indian Ocean (MRU, MLE) and Canary Islands (FUE, ACE) rows; drop them when the user means the continent.

When the right endpoints are a judgment call rather than a region — "somewhere with great diving reachable from NRT" — mark the leg `dests: {discover: {brief: "...", max_airports: N}}`. The compiled graph gains a zero-quota `scout:<leg-id>` node on the research lane; its validated airports feed the leg's sweep beside anything declared. Scout adds endpoints, never gates: buckets and program sweeps on the same leg keep sweeping.

Every sweep asks for all cabins in one call with `include_filtered=true` — cabin is a preference, and the server's dynamic-price filter would otherwise hide the expensive near-misses the judgment lanes exist to weigh.

## Season awareness

Sanity-check each bucket's season inside the window before sweeping it. "Warm, beachy" in September rules out southern-hemisphere winter coasts and rules in the shoulder-season Mediterranean; monsoon calendars split Southeast Asia coast by coast. Shoulder season is usually what the ask wants — award space opens exactly when the crowds leave. Events cut both ways: a festival makes a destination or a layover city more interesting, a city-wide congress blows up the hotel math; both are `destination_context` evidence. Season judgment picks which buckets to sweep — it never suppresses a row after the fact. A bucket that came back cheap in the "wrong" season is a finding to present.

## Affordability and top-ups

`afford --program <slug> --miles <n>` computes the funding position: the program balance, transfer paths in from bank currencies at their ratios, and the shortfall. `--include-purchase` prices buying the shortfall at the program's published rate (`registry points-pricing` has rate, typical sale, and cadence per program). Hotel programs are ordinary registry rows with their own bank transfer paths — Chase into Hyatt, Amex into Hilton, Citi into Choice, and the rest — so a stay's funding position computes the same way a flight's does. Chase and Citi paths are card-gated: each transfer path carries a `card_access` annotation — a qualifying card on file, cards on file for that bank but none qualifying, or unknown when none are recorded — beside the registry note verbatim. The annotation orders confidence in a path and never removes one: a card absent from prefs is not a card the user lacks.

Transfer first. When a transfer covers the gap, name the bank, the amount, and the ratio ("60k of the 80k Chase balance to united at 1:1") before any cash option. Price a purchase only for a small residual on an award already worth it, and present "buy N points for about $X" beside the taxes, citing the rate's source. All of it annotates: an unfunded journey keeps its spot with its top-up path attached. When top-up cost plus taxes approaches the cash fare, say so on the board. Expiring `travel_instruments` — monetary credits, hotel night certificates, companion fares — ride the same evidence lane.

## Journeys

The journey is the unit of search, ranking, and presentation: one concrete leg per intent — award rows, cash quotes, or both on `either` legs — chained into a whole trip. One dispatch plans the whole thing. A chained leg's endpoints resolve mid-run from the prior leg's reached dests — its shortlist landings, plus its declared concrete dests across a cash boundary — and one comma-listed `/search` call sweeps every candidate endpoint. Composition happens before ranking, so combined cost and symmetric per-leg judgment — seat quality, layovers, transit, on the return too — decide the board.

Search results are honest per endpoint: `complete`, `searched_empty`, `partial` (a truncated page is not an empty one), `not_run`, or `failed`. An outbound whose return search came back empty becomes an unpaired lead — a trailing board class ordered by outbound mileage, each showing when its return was searched and how stale that answer is. An expired cached empty reads "unverified", and `partial`/`not_run`/`failed` never read as "no space".

An open jaw is the same single dispatch with explicit origins or dests declared on a later leg — declared endpoints REPLACE the chained anchor wherever they appear; return origins are veto-checked, home destinations are exempt.

## Routing strategies

The founding instruction, verbatim: "lie flat on points to a common airport such as NRT, and then cash... the point is we can get creative with routings". A trip is a composition of legs, not one availability row. Invent the shape, then price it. Every shape competes as a journey in the rank cost lane: same-program journeys band on scalar mileage, mixed-program and cash-bearing journeys meet on a Pareto front where cash is its own axis.

- Direct award — one availability row; the baseline every hybrid must beat.
- Gateway hybrid — a lie-flat award to a hub, a cash ticket onward. Three ordered `plan.legs` express it — an award leg to the gateway, an `either`-mode middle leg onward, then the return — beam-capped and cheap-ranked before any expansion spends quota. Hybrid journeys compose at expand with legs typed `award|cash`, so assess judges the cash hop's layover like any other leg.
- Two-award stitch — a second award onward from the gateway, any program.
- Cash positioning — a cash hop to the award's true origin when it isn't the user's airport. Declare it as a leading `optional: true` cash leg: both variants compose — positioned, and home-origin-direct — and compete on the same front, so the hop is priced, never assumed.
- Long-range positioning — a cheap long cash leg into an award-rich region, then the award from there.
- Hand-built chain — a routing you found yourself, or one the beam cut: declare it in `legs/manual.json` (availability ids plus cash pairs) and `expand` prices it through the same deterministic fit, cost, and miss lanes as any composed journey. A chain skipping an optional leg is legal; a stale chain after a plan edit lands in `manual_rejected` with its reason.

Gateway sets are concrete IATA codes, never pseudo-codes. Seed them from the expansions in `registry regions`; refine per program with `routes <source>` ranked by monitored-route count: one call returns the program's entire route map, so redirect it to a scratch file. When the hub set itself is the judgment call, `dests: {discover: ...}` hands it to a scout node instead. The cabin for each cash or stitched leg is a per-leg judgment call fed by duration fit facts — there is no fixed cutoff. The avoid union never vetoes a gateway: Tokyo on the avoid list kills NRT as an endpoint, not as the hub the award lands at. `trip set` enforces the flip side, rejecting an onward set that intersects the union.

Cash legs price through `getaway bridge <slug> --leg <id>` — the fli driver with the Airport-alias and origin-local-date hardening built in — and each priced quote carries its real departure and arrival clocks, which is what lets a hybrid's lodging interval derive honestly. When the fli driver fails a pair and a SerpApi key is on file (`SERPAPI_API_KEY` or the `serpapi_op_ref` preference), bridge falls back to SerpApi's Google Flights API for that pair — quotes carry `source: fli|serpapi`.

## Hotels

Lodging is in scope only when the ask puts it there: "a week somewhere warm" implies `plan.lodging`; "get me to NYC Tuesday" doesn't. When it is, the compiled graph gains a stays node that runs after journey composition, because a stay's dates come from actual journey timestamps — check-in from the destination arrival, check-out from the return departure or an explicit `plan.lodging.checkout`. A Tuesday return honestly adds the extra hotel night to that journey's board entry. A journey with no known checkout — an unpaired lead, an open jaw without a surface itinerary, a cash arrival the quote never carried — defers lodging with its reason; nothing ever guesses a night.

The data source is rooms.aero, seats.aero's hotel product: six programs (Hyatt, Hilton, Marriott, IHG, Choice, Wyndham), no public API, driven by one browser agent through the seeded Pro session (`agent-browser-with-cookies`; the walker preflights the session before dispatch). The walk searches each eligible journey's exact interval and pipes normalized rows to `stays ingest`. rooms.aero prices blocks of at most five consecutive nights, so a longer stay clamps to five with the clamp disclosed. Per-night points and cash (in the property's local currency) are the source of truth — any stay total is an estimate. Only the Pro session refreshes stale rows, so freshness stamps and `stale` flags travel to the board. Stays spend zero seats.aero quota and never wait on the quota floor.

## Presenting options

Present the board as a live cc-present artifact (the `cc-present:present` skill). The plugin ships the `getaway` block pack; every block and field is documented in [.claude/components/reference/blocks.md](../../../.claude/components/reference/blocks.md). Rows come from `trip artifact read <slug> finalists.json`, whose result classes map straight onto the board:

- Ranked journeys (at most six, post-assess): one `getaway.option-picker` for the set, `optionId` keyed by journey id; per journey, a `getaway.itinerary` per award leg fed only from expanded detail — integer miles, taxes as integer minor units with a currency, remaining seats, booking link, and `UpdatedAt` as the freshness stamp — and a `getaway.flight` with `price` for each cash leg. Costs stay per program plus cash, unpriced components named; never sum across programs.
- Preference misses always render. Every journey's `preference_misses` annotations ("+1 day past your Monday preference") appear in its evidence section verbatim — the renderer guarantees this, not the model's discretion.
- Notable stretches — assess-selected journeys from beyond the cut whose excellence outweighs a miss — get their own short section, each with the miss named beside what makes it worth it.
- Unpaired outbound leads trail the board, ordered by outbound mileage, each line carrying when its return search ran and the cache age. "No J space home as of Tuesday" is a lead, not a verdict, and an expired empty reads "unverified".
- Verification annotations from `enhance-verify.json` ride the rows they checked: "verified live 14:32" beside the freshness stamp on a confirmed row, a prominent gone-on-`<host>` warning on a row the live site killed — the journey keeps its board spot, demoted in-tier, never silently dropped. A rescued lead — an empty return a verifier found space on — carries the rescue note in its leads line.
- A `partial`, `not_run`, or `failed` return search surfaces as exactly that, with its reason — never as "no space".
- Stays ride one `getaway.stay` block per journey. A walked entry maps fields verbatim: `provenance.session` fills `session`, `provenance.fetched_at` fills `checkedAt`, `provenance.night_clamped` fills `interval.nightClamped` (pass the journey's true nights as `requestedNights` when clamped), the entry's `search_state` fills `searchState`, `destination.query` fills `destination`, each room's `last_checked_at` fills its `checkedAt`, and registry slugs resolve to display names. A deferred `lodging_search` maps to the block's `deferred` state with its reason. The block owns the honesty invariants; the agent only maps fields.
- A `getaway.availability` grid when the user asks about other dates or cabins — built from `cache query`, zero API calls; a tapped cell asks for that date and cabin expanded.
- Beside the picker, one evidence `section` block per journey: one line per active factor from its verdict map — funding position, seat product (a `barely` verdict phrased as a warning), the layover line covering both duration and how interesting the city is, cash-anomaly notes, and the fit lines. A `demote` verdict reads as a warning, not a footnote. Pack schemas are closed: evidence rides `section` blocks, never extra fields on pack blocks.

`AskUserQuestion` stays the lightweight path — at most 4 quick questions in one call — when a full board is overkill.
