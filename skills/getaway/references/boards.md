# Board shapes

The four boards a trip moves through, each a phase of one live cc-present
artifact. [planning.md](planning.md), "The board flow", owns the spine and the
event protocol; this file owns the shapes — the envelope, the blocks, and the
JSON that validates. Built-in block fields are the `cc-present:present` skill's
`reference/blocks.md`; the `getaway.*` pack fields are
[.claude/components/reference/blocks.md](../../../.claude/components/reference/blocks.md).

## One artifact, four phases

A trip is one artifact. Each phase is a round on it: the intake round opens it,
a submit closes a round and the next phase's `push` fills the round the submit
opened. The phases run intake, then Finalists, then Head to head, then Booking
sheet, and a single shortlist pick skips Head to head straight to the Booking
sheet.

Ids namespace by phase — `in-`, `fin-`, `cmp-`, `book-`. A reused id inherits
the prior round's verdict, so every phase mints fresh ids; never carry `fin-j2`
into the comparison round as `fin-j2`.

**Never full-push mid-round.** A `push` stamps the whole document into the
current round, collapsing the live blocks a verifier or a reply is still
annotating. Inside a round, only `update-block`, `reply`, and `remove-block`
touch the board. A full `push` belongs at exactly one place: the phase
transition, filling the round a submit just opened.

## Phase transition

On a `submit`, drain and advance in one sequence at the main level:

```bash
cc-present outcomes --no-doc --session "$CLAUDE_CODE_SESSION_ID"   # verdicts, picks, feedback
# apply writes: trip set / trip log; answer any un-replied notes with reply
cc-present round --title "Head to head"                            # names the round the submit opened
cc-present push "$NEXT_DOC"                                        # fills it with the next phase
```

`round --title` right after a submit only titles the round already opened by the
submit; it never advances twice. The `push` that follows is the one full push
the round allows.

## Composition

Cards are decision units and top level only; a card nests leaf blocks one level
deep. A journey card wraps its pack blocks plus an evidence markdown leaf —
`getaway.itinerary` per award leg, `getaway.flight` per cash leg, `getaway.stay`
for lodging, then a `markdown` carrying the verdict lines. The card is the unit
the human scans; the leaves are its contents.

A board that carries a decision unit opens in focus mode by default, one card at
a time. Every `getaway.*` pack block is a decision unit — each carries a `note`
interaction — as are the built-in `choice` and `approval`. All four boards carry
pack blocks or a choice, so every one sets `"presentation": "board"` to stay
scannable, the booking sheet included: it has no `submit`, but its `getaway.booking`
and `getaway.stay` blocks are interactive, so without the hint it would open as a
one-card deck.

## Board 1 — intake

Replaces the opening `AskUserQuestion`. Every block is a built-in; the intake
board carries no pack blocks. `presentation` is `board`, stats are balance chips
plus the quota, and the submit starts the search.

```json
{
  "version": 1,
  "title": "Planning: warm, beachy week on points",
  "intro": "Here's what I know, and what I need. Adjust anything, leave the rest blank, and press Start the search.",
  "presentation": "board",
  "stats": [
    { "label": "Aeroplan", "value": "88,000" },
    { "label": "Alaska", "value": "90,000" },
    { "label": "quota left", "value": "1,000" }
  ],
  "submit": { "label": "Start the search", "note": "I sweep 28 programs and come back with ranked journeys." },
  "blocks": [
    {
      "id": "in-context",
      "type": "markdown",
      "md": "**What I know.** Home: SFO, SJC, SAN, PDX. Top balances: 90k Alaska, 88k Aeroplan, 150k Amex. Avoid landing at: ICN, GMP, NRT, HND. Expiring: Alaska companion fare, 2027-06-30. No open trips."
    },
    {
      "id": "in-dest",
      "type": "choice",
      "prompt": "Where should I sweep? Pick any that appeal — I price the rest as leads.",
      "multi": true,
      "options": [
        {
          "id": "hawaii",
          "label": "Hawaii",
          "hint": "shortest lie-flat beach on points",
          "facts": [
            { "label": "season", "value": "dry, warm", "tone": "good" },
            { "label": "flight", "value": "5–6h" },
            { "label": "coverage", "value": "Alaska, American" }
          ],
          "detail": {
            "pros": ["Nonstop award space from the west coast", "In-season and dry in September"],
            "cons": ["Business award seats are thin outside Alaska"]
          }
        },
        {
          "id": "mexico-pacific",
          "label": "Pacific Mexico",
          "hint": "warm, cheap, close",
          "facts": [
            { "label": "season", "value": "humid, warm" },
            { "label": "flight", "value": "4–5h" },
            { "label": "coverage", "value": "Aeroplan, Alaska" }
          ],
          "detail": {
            "pros": ["Cheapest business awards in the set"],
            "cons": ["Tail end of the rainy season in the window"]
          }
        }
      ]
    },
    { "id": "in-dest-other", "type": "input", "label": "Somewhere else warm? (comma-separated cities or airports)", "placeholder": "flexible" },
    {
      "id": "in-window",
      "type": "choice",
      "prompt": "Which window works?",
      "multi": true,
      "options": [
        { "id": "early-sep", "label": "Sep 6 – Sep 20", "hint": "your stated range", "facts": [{ "label": "nights", "value": "7" }] },
        { "id": "late-sep", "label": "Sep 20 – Oct 4", "hint": "shoulder, cheaper award space", "facts": [{ "label": "nights", "value": "7" }] }
      ]
    },
    { "id": "in-dates", "type": "input", "label": "Exact dates instead? (YYYY-MM-DD to YYYY-MM-DD)", "placeholder": "flexible" },
    {
      "id": "in-cabin",
      "type": "choice",
      "prompt": "Which cabins should compete?",
      "multi": true,
      "options": [
        { "id": "business", "label": "Business", "hint": "primary — what you asked for", "facts": [{ "label": "priority", "value": "primary", "tone": "good" }] },
        { "id": "premium", "label": "Premium economy", "hint": "falls back to it when business is thin" },
        { "id": "economy", "label": "Economy", "hint": "priced as a floor" }
      ]
    },
    { "id": "in-party", "type": "input", "label": "How many travelling?", "placeholder": "2" },
    {
      "id": "in-scope",
      "type": "choice",
      "prompt": "What should I plan?",
      "multi": true,
      "options": [
        { "id": "flights", "label": "Flights", "hint": "always on" },
        { "id": "hotel", "label": "Hotel award nights", "hint": "rooms.aero at the destination" },
        { "id": "creative", "label": "Open to creative routings", "hint": "a cash positioning hop, an award stitched across programs" }
      ]
    },
    { "id": "in-constraints", "type": "input", "label": "Hard constraints only — blank means flexible", "placeholder": "none", "multiline": true },
    { "id": "in-notes", "type": "input", "label": "Anything else I should weigh?", "placeholder": "none", "multiline": true }
  ]
}
```

Read the outcomes back with the onboard form discipline
([skills/onboard/SKILL.md](../../onboard/SKILL.md), "Confirm in the form"):
placeholders are seeded from `prefs show`, a field absent from the outcomes
stayed blank so its placeholder value is kept, and a preference never gets
overwritten with an empty value. A typed `in-constraints` line lands in
`constraints` with `confirmed: true` — the one hop that satisfies the constraint
doctrine, since the human wrote it into a field labelled hard-constraints-only.
After the drain, `trip new`, one `trip set`, and `trip profile`, then compile
and dispatch. Upsert a `progress` block before dispatch and mark it `done` when
finalists land:

```json
{ "id": "in-progress", "type": "progress", "label": "Sweeping 28 programs", "value": 0, "max": 28, "state": "active" }
```

## Board 2 — finalists

The finalist round, pushed after dispatch. Stats are run counters; a
`fin-sweep` markdown header carries the run's provenance from the `sweep_summary`
key in `finalists.json` when present; `fin-shortlist` is a built-in multi-choice,
one option per journey including notable stretches; each `fin-j<i>` card wraps
the journey; `fin-leads` trails with the unpaired leads.

```json
{
  "version": 1,
  "title": "Warm, beachy week — finalists",
  "intro": "Ranked journeys from a live sweep. Pick one to book, several to compare, or leave a note under any card.",
  "presentation": "board",
  "stats": [
    { "label": "programs swept", "value": "28" },
    { "label": "journeys expanded", "value": "34" },
    { "label": "quota spent", "value": "61" },
    { "label": "cache age", "value": "2h" }
  ],
  "submit": {
    "label": "Send my pick",
    "note": "Several picks and I verify and compare them; one pick and I go straight to the booking sheet."
  },
  "blocks": [
    {
      "id": "fin-sweep",
      "type": "markdown",
      "md": "Swept Alaska, American, Aeroplan, and 25 more across Aug 30 – Oct 4, soft dates padded seven days past your Sep 6 window. Balances cover every ranked option. Availability read 2 hours ago."
    },
    {
      "id": "fin-shortlist",
      "type": "choice",
      "prompt": "Which journeys are in play?",
      "multi": true,
      "options": [
        {
          "id": "j1",
          "label": "Honolulu — 62k, Alaska + American",
          "hint": "cheapest; misses business both ways",
          "facts": [
            { "label": "miles", "value": "12.5k AS + 49.5k AA" },
            { "label": "taxes", "value": "$11.20" },
            { "label": "cabin", "value": "economy / premium", "tone": "warn" },
            { "label": "dates", "value": "Aug 31 – Sep 6" },
            { "label": "seats", "value": "9" }
          ],
          "detail": {
            "pros": ["Both balances cover it outright", "Nonstop each way"],
            "cons": ["Below your business preference on both legs"]
          }
        },
        {
          "id": "stretch",
          "label": "Honolulu all-business — 70k Alaska",
          "hint": "stretch: clears business, pays with a risky-short LAX connection",
          "facts": [
            { "label": "miles", "value": "70k AS" },
            { "label": "taxes", "value": "$51.20" },
            { "label": "cabin", "value": "business", "tone": "good" },
            { "label": "dates", "value": "Aug 30 – Sep 6" },
            { "label": "seats", "value": "2" }
          ],
          "detail": {
            "pros": ["The one option that clears your business preference", "Boeing 777-300ER business on both transpacific legs"],
            "cons": ["45-minute LAX connection, 30 under your floor"]
          }
        }
      ]
    },
    {
      "id": "fin-j-stretch",
      "type": "card",
      "title": "Stretch · Honolulu all-business — 70,000 Alaska miles",
      "summary": "The one finalist that clears business; the outbound runs a risky-short Los Angeles connection.",
      "chips": [{ "label": "business", "tone": "demo" }, { "label": "stretch", "tone": "flag" }],
      "children": [
        {
          "id": "fin-j-stretch-out",
          "type": "getaway.itinerary",
          "program": "Alaska Atmos Rewards",
          "miles": 35000,
          "taxes": [{ "amount": 2560, "currency": "USD" }],
          "remainingSeats": 2,
          "bookingLinks": [{ "label": "Book via Alaska", "url": "https://www.alaskaair.com/search/results?O=SAN&D=HNL&OD=2026-08-30", "primary": true }],
          "fetchedAt": "2026-07-13T19:32:13Z",
          "totalDurationMinutes": 455,
          "segments": [
            { "flightNumber": "AA2401", "origin": "SAN", "destination": "LAX", "departsAt": "2026-08-30T07:00:00", "arrivesAt": "2026-08-30T07:55:00", "cabin": "first", "aircraft": "Airbus A321", "aircraftCode": "321", "durationMinutes": 55 },
            { "flightNumber": "AA103", "origin": "LAX", "destination": "HNL", "departsAt": "2026-08-30T08:40:00", "arrivesAt": "2026-08-30T11:35:00", "cabin": "business", "aircraft": "Boeing 777-300ER", "aircraftCode": "77W", "seatQuality": { "verdict": "solid", "product": "Super Diamond era, Flagship Suite retrofit from Feb 2026", "note": "Retrofit completes 2027; refitted frames rate suite." }, "durationMinutes": 355 }
          ]
        },
        {
          "id": "fin-j-stretch-ev",
          "type": "markdown",
          "md": "**Why it boarded**, the run's note verbatim: \"clears your business cabin preference — every ranked finalist misses it.\"\n\n- ✅ **Affordability** — 70,000 Alaska miles; your balance covers.\n- ✅ **Seat** — Boeing 777-300ER business (registry: solid) on both transpacific legs.\n- ⚠️ **Layovers** — demote: \"Risky-short 45-min LAX connection — 30 min under your 75-min floor.\""
        }
      ]
    },
    {
      "id": "fin-leads",
      "type": "markdown",
      "md": "**Unpaired leads.** Kona (KOA) had a 25,000-mile Alaska outbound but no business return in the window; return searched 2 hours ago, `searched_empty`. A lead, not a verdict — an expired empty reads unverified, not \"no space\"."
    }
  ]
}
```

An availability grid rides the board on request only. When a note asks about
other dates or cabins, answer it from `cache query` — zero quota — and
`update-block` a `getaway.availability` grid under the journey it belongs to; a
tapped cell streams back a `pack.interaction` asking for that date and cabin
expanded.

## Board 3 — head to head

Pushed the moment a shortlist submit carries two or more picks. Drain, answer
outstanding notes, `expand detail --cabin` each pick, kick the `verify` and
`seat-advice` enhancers plus a stays refresh scoped to the picks, then push
immediately with what is known — verifiers annotate the live board as they land
(`update-block` plus `reply`), a `gone` flag rides prominently, and the row
stays visible. `cmp-table` is one column per pick; `cmp-j<id>` is a refreshed
card per pick; `cmp-final` is a single-choice lock-in.

```json
{
  "version": 1,
  "title": "Head to head",
  "intro": "Your picks side by side. Numbers firm up as live checks land. Lock one in when you're ready.",
  "presentation": "board",
  "stats": [{ "label": "picks", "value": "2" }, { "label": "verified", "value": "1 of 2" }],
  "submit": { "label": "Lock it in", "note": "I build the booking sheet for the journey you lock." },
  "blocks": [
    {
      "id": "cmp-table",
      "type": "table",
      "columns": [
        { "key": "row", "label": "·" },
        { "key": "stretch", "label": "All-business" },
        { "key": "cheap", "label": "Cheapest" }
      ],
      "rows": [
        { "row": "**Miles**", "stretch": "70k Alaska", "cheap": "12.5k Alaska + 49.5k American" },
        { "row": "**Transfer**", "stretch": "none — balance covers", "cheap": "none — balances cover" },
        { "row": "**Taxes**", "stretch": "$51.20", "cheap": "$11.20" },
        { "row": "**Cabin**", "stretch": "business · 777-300ER · Flagship", "cheap": "economy / premium" },
        { "row": "**Duration**", "stretch": "7h35m · 1 stop", "cheap": "5h58m · nonstop" },
        { "row": "**Verified**", "stretch": "live 14:32 ✓", "cheap": "pending" }
      ]
    },
    {
      "id": "cmp-j-stretch",
      "type": "card",
      "title": "All-business — 70,000 Alaska miles",
      "chips": [{ "label": "verified live 14:32", "tone": "demo" }],
      "children": [
        {
          "id": "cmp-j-stretch-out",
          "type": "getaway.itinerary",
          "program": "Alaska Atmos Rewards",
          "miles": 35000,
          "taxes": [{ "amount": 2560, "currency": "USD" }],
          "remainingSeats": 2,
          "bookingLinks": [{ "label": "Book via Alaska", "url": "https://www.alaskaair.com/search/results?O=SAN&D=HNL&OD=2026-08-30", "primary": true }],
          "fetchedAt": "2026-07-18T14:32:00Z",
          "totalDurationMinutes": 455,
          "segments": [
            { "flightNumber": "AA2401", "origin": "SAN", "destination": "LAX", "departsAt": "2026-08-30T07:00:00", "arrivesAt": "2026-08-30T07:55:00", "cabin": "first", "aircraft": "Airbus A321", "aircraftCode": "321", "durationMinutes": 55 },
            { "flightNumber": "AA103", "origin": "LAX", "destination": "HNL", "departsAt": "2026-08-30T08:40:00", "arrivesAt": "2026-08-30T11:35:00", "cabin": "business", "aircraft": "Boeing 777-300ER", "aircraftCode": "77W", "seatQuality": { "verdict": "solid", "product": "Super Diamond era, Flagship Suite retrofit from Feb 2026", "note": "Verified live: 2 business seats at 14:32." }, "durationMinutes": 355 }
          ]
        }
      ]
    },
    {
      "id": "cmp-final",
      "type": "choice",
      "prompt": "Which one do I book?",
      "multi": false,
      "options": [
        { "id": "stretch", "label": "All-business", "hint": "clears your cabin; live-verified", "facts": [{ "label": "miles", "value": "70k" }, { "label": "seats", "value": "2", "tone": "good" }] },
        { "id": "cheap", "label": "Cheapest", "hint": "half the miles, misses business", "facts": [{ "label": "miles", "value": "62k" }, { "label": "cabin", "value": "economy", "tone": "warn" }] }
      ]
    }
  ]
}
```

## Board 4 — booking sheet

The final round: a full push of the whole locked journey. It has no `submit`,
but its `getaway.booking` and `getaway.stay` blocks carry `note` interactions,
so every block is a decision unit; set `"presentation": "board"` or the sheet
opens as a one-card deck. The host still renders its submit bar with a decided
tally over those note-only blocks — cosmetic dots, nothing gated. A Submit on
this round is the booked-it confirmation: drain `outcomes`, answer any last
notes, and close. The board stays open for live Q&A while the human
books. The
`getaway.booking` block carries the whole journey: every leg in chain order,
award and cash, with `totals`, `transfers`, and per-flight `seat` picks (each
`{seat, why}`). When the balance covers the award outright, `transfers` is `[]`
and `book-notes` says so. `book-stay` maps the journey's lodging; `book-notes`
carries the caveats the block's fields don't — per-program tax totals never
cross-summed, check-in timing, travel documents, and expiring instruments
applied. Close on confirmation with
`close --summary "<journey one-liner>; sheet delivered"`.

```json
{
  "version": 1,
  "title": "Booking sheet — Honolulu all-business",
  "intro": "70,000 Alaska miles, San Diego to Honolulu in business via Los Angeles, home the same way. Everything you need to book, in order. Ask me anything while you go.",
  "presentation": "board",
  "blocks": [
    {
      "id": "book-sheet",
      "type": "getaway.booking",
      "title": "Book San Diego to Honolulu, all business",
      "subtitle": "One Alaska award, San Diego to Honolulu in Boeing 777-300ER business via Los Angeles",
      "fetchedAt": "2026-07-18T14:32:00Z",
      "totals": {
        "miles": [{ "program": "Alaska Atmos Rewards", "miles": 70000 }],
        "cash": [{ "amount": 5120, "currency": "USD" }]
      },
      "transfers": [],
      "legs": [
        {
          "role": "Outbound",
          "kind": "award",
          "program": "Alaska Atmos Rewards",
          "miles": 35000,
          "taxes": [{ "amount": 2560, "currency": "USD" }],
          "flights": [
            { "flightNumber": "AA2401", "origin": "SAN", "destination": "LAX", "departsAt": "2026-08-30T07:00:00", "arrivesAt": "2026-08-30T07:55:00", "cabin": "first", "durationMinutes": 55, "aircraft": "Airbus A321", "aircraftCode": "321" },
            { "flightNumber": "AA103", "origin": "LAX", "destination": "HNL", "departsAt": "2026-08-30T08:40:00", "arrivesAt": "2026-08-30T11:35:00", "cabin": "business", "durationMinutes": 355, "aircraft": "Boeing 777-300ER", "aircraftCode": "77W", "seat": { "verdict": "solid", "product": "Super Diamond era, Flagship Suite retrofit from Feb 2026", "picks": [{ "seat": "9A", "why": "true window, direct aisle access" }], "avoids": [{ "seat": "12D", "why": "bulkhead bassinet position" }] } }
          ],
          "bookingLinks": [{ "label": "Book the award on Alaska", "url": "https://www.alaskaair.com/search/results?O=SAN&D=HNL&OD=2026-08-30", "primary": true }],
          "notes": ["Tight 45-minute Los Angeles connection — one award, so it protects, but leave nothing to check."]
        },
        {
          "role": "Return",
          "kind": "award",
          "program": "Alaska Atmos Rewards",
          "miles": 35000,
          "taxes": [{ "amount": 2560, "currency": "USD" }],
          "flights": [
            { "flightNumber": "AA104", "origin": "HNL", "destination": "LAX", "departsAt": "2026-09-06T09:00:00", "arrivesAt": "2026-09-06T17:05:00", "cabin": "business", "durationMinutes": 305, "aircraft": "Boeing 777-300ER", "aircraftCode": "77W", "seat": { "verdict": "solid", "product": "Super Diamond era, Flagship Suite retrofit from Feb 2026", "picks": [{ "seat": "9L", "why": "true window, direct aisle access" }], "avoids": [{ "seat": "12G", "why": "bulkhead bassinet position" }] } },
            { "flightNumber": "AA2530", "origin": "LAX", "destination": "SAN", "departsAt": "2026-09-06T18:30:00", "arrivesAt": "2026-09-06T19:25:00", "cabin": "first", "durationMinutes": 55, "aircraft": "Airbus A321", "aircraftCode": "321" }
          ],
          "bookingLinks": [{ "label": "Book the award on Alaska", "url": "https://www.alaskaair.com/search/results?O=HNL&D=SAN&OD=2026-09-06", "primary": true }],
          "notes": ["Same 777-300ER business seat home."]
        }
      ]
    },
    {
      "id": "book-stay",
      "type": "getaway.stay",
      "state": "searched",
      "destination": "Honolulu",
      "airport": "HNL",
      "session": "pro",
      "checkedAt": "2026-07-18T14:20:00Z",
      "searchState": "night_clamped",
      "interval": { "checkIn": "2026-08-30", "checkOut": "2026-09-04", "nights": 5, "nightClamped": true, "requestedNights": 7 },
      "rooms": [
        {
          "program": "World of Hyatt",
          "name": "Hyatt Regency Waikiki Beach",
          "currency": "USD",
          "checkedAt": "2026-07-18T14:20:00Z",
          "stale": false,
          "offers": [{ "awardClass": "standard", "pointsPerNight": 25000, "cashPerNightCents": 42000, "centsPerPoint": 1.68 }]
        }
      ]
    },
    {
      "id": "book-notes",
      "type": "markdown",
      "md": "**Before you book.** Taxes are $51.20 across the two Alaska awards — one program, no cross-summing. Your 90,000-mile Alaska balance covers the 70,000 outright, so no transfer. A US passport or green card covers this domestic travel. Your Alaska companion fare (expires 2027-06-30) does not apply to award tickets. rooms.aero clamped the 7-night stay to its 5-night maximum, so book the last two nights separately."
    }
  ]
}
```
