# getaway pack blocks

The `getaway` block pack renders award-travel results inside cc-present boards. It ships five block types, each referenced by its dotted wire `type` inside any `Doc.blocks` array or a card's `children`:

| Block | Dotted type | Renders |
|-------|-------------|---------|
| Itinerary | `getaway.itinerary` | One bookable award option with its full segment list. |
| Flight | `getaway.flight` | A single leg — an award leg or a cash positioning leg. |
| Availability | `getaway.availability` | A date × cabin mileage grid for one market. |
| Stay | `getaway.stay` | One journey's lodging, or an honest deferral. |
| Booking | `getaway.booking` | The final booking sheet for a locked trip. |

Pack `host_api` is 2.

## Conventions

These hold across every block.

**Money** is a `{amount, currency}` object: `amount` is an integer in minor currency units, `currency` an ISO 4217 code. `{amount: 18560, currency: "USD"}` renders $185.60. The CLI's `fli` returns major-unit floats — convert before composing, so `305.0` USD becomes `{amount: 30500, currency: "USD"}`.

**Taxes are a list, never a single number.** An itinerary or booking leg carries `taxes` as an array of tax lines, each `{amount, currency}`. Lines group and render per currency; figures in different currencies are never summed into one total. A `12050 USD` line beside a `37500 ZAR` line shows as `$120.50 + ZAR 375`, never a single blended figure.

**Wall-clock times render verbatim.** `departsAt` and `arrivesAt` are local wall-clock timestamps, never timezone-converted. A trailing `Z` from seats.aero is accepted and ignored. Arrival on a different local date renders a day suffix: `+1`, `+2`, or `-1` for an eastbound dateline crossing.

**Freshness is a real instant.** `fetchedAt` (itinerary, booking) and `checkedAt` (stay, and each stay room) are true UTC instants — the CLI's own fetch timestamp, the journey leg's `fetched_at` — and render as relative age, such as "6 hours ago". A cache-served detail still carries the store's original fetch stamp, so `fetched_at` is always a real instant; there is no null case. They require seconds and a `Z` or offset. This is distinct from the wall-clock departure and arrival times above, which render as written.

**Cabin** values are `economy`, `premium`, `business`, or `first`; airports are uppercase IATA codes, and durations are integer minutes.

## Notes and replies

Every pack block is interactive through a `note` field — free-form markdown up to 2000 characters. When the human submits, the value arrives as a `pack.interaction` event keyed by the block `id`. The payload matches the block's interaction schema: note-only blocks (itinerary, flight, stay, booking) send `{note}`; availability sends `{picks, note?}`.

Submits merge. A later submit carries the whole accumulated object, so a note added to an availability block that already holds picks arrives as `{picks: [...], note: "..."}` — the note rides beside the picks, it does not clear them.

Answer a note with `cc-present reply --block <id> --body <md>`. The reply renders under that block; any block accepts one. Once a round closes, the note affordance is hidden.

## Focus-mode composition

Every pack block declares an interaction schema, so each block is its own step in a focus deck. Compose to that grain:

- Wrap each finalist's blocks in a titled `card` — one card per finalist — so every step carries a proper peek title.
- Set `"presentation": "board"` on a content-only push (the final booking sheet, a dashboard) so it renders as a board instead of stepping.
- Omit the presentation hint on a decision round, where stepping through each block one at a time is the point.

`examples/board.json` shows a full board: a `card` wrapping an itinerary and its evidence, a `choice` shortlist, and a `getaway.booking` sheet.

## getaway.itinerary

One bookable award option with its full segment list.

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.itinerary"` | yes | The dotted wire type. |
| `program` | string | yes | Mileage program display name, such as "Aeroplan". |
| `miles` | integer ≥ 1 | yes | Integer mileage cost. |
| `taxes` | taxLine[] | yes | Per-currency tax lines; may be empty. |
| `taxesNote` | string | no | Prose shown when `taxes` is empty. |
| `remainingSeats` | integer ≥ 0 | yes | Seats left, shown in the meta line. |
| `bookingLinks` | bookingLink[], min 1 | yes | One entry carries `primary: true`. |
| `fetchedAt` | UTC instant | yes | The leg's `fetched_at`; renders as relative age. |
| `totalDurationMinutes` | integer ≥ 1 | yes | End-to-end journey time. |
| `segments` | segment[], min 1 | yes | Rendered in array order. |

Each tax line (`taxes[]`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `amount` | integer ≥ 0 | yes | Minor units. |
| `currency` | ISO 4217 | yes | The line's currency. |

Each booking link (`bookingLinks[]`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `label` | string | yes | Link text. |
| `url` | string | yes | Must start `https://`; opens in a new tab. |
| `primary` | boolean | yes | Exactly one link is the primary action. |

Each segment (`segments[]`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `flightNumber` | string | yes | Carrier code plus number, like `QR738`. |
| `origin`, `destination` | IATA | yes | |
| `departsAt`, `arrivesAt` | wall clock | yes | |
| `cabin` | cabin | yes | |
| `aircraft` | string | yes | Display name, such as "Boeing 777-300ER". |
| `aircraftCode` | string | no | IATA equipment code, like `77W`. |
| `seatQuality` | seatQuality | no | Registry verdict for this segment. |
| `durationMinutes` | integer ≥ 1 | yes | |

`seatQuality` (shared with `getaway.flight`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `verdict` | `suite` \| `solid` \| `dated` \| `barely` \| `verify` | yes | The seat's quality tier. |
| `product` | string \| null | no | Named product, such as "Qsuite". |
| `note` | string \| null | no | One-line seat advice. |

The header route derives from the first segment's `origin` and the last segment's `destination`. A layover divider with the computed gap appears between segments that share an airport (`prev.destination` equals `next.origin`); an open jaw renders a plain divider.

**Interaction:** `{note}` (required, ≤ 2000 chars). Note-only.

**Example** — `examples/itinerary.json`:

```json
{
  "id": "itin-sfo-cpt-qr",
  "type": "getaway.itinerary",
  "program": "Aeroplan",
  "miles": 88000,
  "taxes": [
    { "amount": 12050, "currency": "USD" }
  ],
  "remainingSeats": 3,
  "bookingLinks": [
    { "label": "Book on Air Canada", "url": "https://www.aircanada.com/us/en/aco/home/book.html", "primary": true },
    { "label": "Verify on Qatar Airways", "url": "https://www.qatarairways.com/en-us/homepage.html", "primary": false }
  ],
  "fetchedAt": "2026-07-11T02:10:00Z",
  "totalDurationMinutes": 1655,
  "segments": [
    {
      "flightNumber": "QR738",
      "origin": "SFO",
      "destination": "DOH",
      "departsAt": "2026-09-06T17:30",
      "arrivesAt": "2026-09-07T19:45",
      "cabin": "business",
      "aircraft": "Boeing 777-300ER",
      "aircraftCode": "77W",
      "seatQuality": { "verdict": "suite", "product": "Qsuite", "note": "Choose an odd-numbered window suite for the most privacy." },
      "durationMinutes": 975
    },
    {
      "flightNumber": "QR1369",
      "origin": "DOH",
      "destination": "CPT",
      "departsAt": "2026-09-07T21:50",
      "arrivesAt": "2026-09-08T06:05",
      "cabin": "business",
      "aircraft": "Airbus A350-900",
      "aircraftCode": "359",
      "seatQuality": { "verdict": "suite", "product": "Qsuite", "note": "Verify the operating aircraft before transferring points." },
      "durationMinutes": 555
    }
  ]
}
```

**Composition** — from one award leg's `finalists.json` entry and its trip detail:

- `miles` ← the leg detail's mileage.
- `taxes` ← `[{amount: detail.total_taxes, currency: detail.taxes_currency}]` when `taxes_currency` is non-null; otherwise `[]` plus a `taxesNote` reading "<program> does not report a taxes currency". Never invent a currency.
- `bookingLinks` ← detail `booking_links`, mapping each CLI `link` to the block's `url`. Exactly one entry carries `primary: true`, the CLI's primary flag.
- `fetchedAt` ← the leg's `fetched_at`.
- `remainingSeats` ← the detail.
- `seatQuality` per segment ← the entry's `seat_advice` row matching on (`carrier`, `aircraft_code`, `cabin`): its registry `verdict`, `product`, and `note`. Live `picks` and `avoids` are reserved for the booking block.

Each `segments` entry maps from one detail segment, key by key. `origin`, `cabin`, and `aircraft` keep their names; the rest rename, so following the doc literally yields a schema-valid segment:

| CLI segment key | Block key |
|-----------------|-----------|
| `flight_number` | `flightNumber` |
| `dest` | `destination` |
| `departs_local` | `departsAt` |
| `arrives_local` | `arrivesAt` |
| `aircraft_code` | `aircraftCode` |
| `duration_minutes` | `durationMinutes` |

The detail segment also carries `carrier` (the `flight_number` prefix), which drives seat matching, not the block. Registry verdicts key on that marketing carrier until the researcher lane reports better: on a codeshare the researcher records the operator as `observed.operated_by`, the finalize fold re-keys the verdict on it and promotes `operated_by` onto the advice row, and the booking flight carries it as `operatedBy`. An unmatched probe still yields verdict `verify` — the honest render.

## getaway.flight

A single leg on its own: an award leg with aircraft and seat quality, or a cash positioning leg with a price.

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.flight"` | yes | The dotted wire type. |
| `flightNumber` | string | yes | Carrier code plus number. |
| `origin`, `destination` | IATA | yes | |
| `departsAt`, `arrivesAt` | wall clock | yes | |
| `cabin` | cabin | yes | |
| `durationMinutes` | integer ≥ 1 | yes | |
| `aircraft` | string | no | Absent on a cash leg. |
| `aircraftCode` | string | no | IATA equipment code. |
| `seatQuality` | seatQuality | no | Registry verdict; see the itinerary table. |
| `price` | money | no | Present on a cash positioning leg only. |

**Interaction:** `{note}` (required, ≤ 2000 chars). Note-only.

**Example** — an award leg, `examples/flight.json`:

```json
{
  "id": "flt-sfo-doh-qr738",
  "type": "getaway.flight",
  "flightNumber": "QR738",
  "origin": "SFO",
  "destination": "DOH",
  "departsAt": "2026-09-06T17:30",
  "arrivesAt": "2026-09-07T19:45",
  "cabin": "business",
  "durationMinutes": 975,
  "aircraft": "Boeing 777-300ER",
  "aircraftCode": "77W",
  "seatQuality": { "verdict": "suite", "product": "Qsuite", "note": "Choose an odd-numbered window suite for the most privacy." }
}
```

**Example** — a cash positioning leg, `examples/flight-positioning.json`:

```json
{
  "id": "flt-lax-sfo-positioning",
  "type": "getaway.flight",
  "flightNumber": "UA1682",
  "origin": "LAX",
  "destination": "SFO",
  "departsAt": "2026-09-06T12:15",
  "arrivesAt": "2026-09-06T13:45",
  "cabin": "economy",
  "durationMinutes": 90,
  "price": { "amount": 30500, "currency": "USD" }
}
```

**Composition:** a cash leg comes from the journey's cash-leg quote — `price` is `{amount, currency}` from that quote, and the leg carries no aircraft, which is normal. An award leg mirrors an itinerary segment: `aircraft`, `aircraftCode`, and `seatQuality`.

## getaway.availability

A date × cabin mileage grid for one market.

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.availability"` | yes | The dotted wire type. |
| `origin`, `destination` | IATA | yes | One market per grid. |
| `program` | string | no | Header badge for single-program sweeps; omit when mixed. |
| `rows` | row[], min 1 | yes | One row per date. |

Each row is `{date, cabins}`: `date` is `YYYY-MM-DD`, and `cabins` maps cabin names to cells with at least one entry. Only the four cabin keys are allowed. An absent cabin key means no space that day and renders as a dash, not a button.

Each cell (`cabins.<cabin>`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `miles` | integer ≥ 1 | yes | Cheapest award at that date and cabin. |
| `seats` | integer ≥ 0 | yes | Remaining seats. |
| `direct` | boolean | yes | `true` renders a nonstop tag. |

**Interaction:** `{picks, note?}`, at least one property present.

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `picks` | `{date, cabin}[]` | no | Selected cells. Toggles accumulate; `picks: []` clears the selection. |
| `note` | string ≤ 2000 | no | Free-form markdown, riding the same object as `picks`. |

**Example** — `examples/availability.json`:

```json
{
  "id": "avail-sfo-fra",
  "type": "getaway.availability",
  "origin": "SFO",
  "destination": "FRA",
  "program": "Aeroplan",
  "rows": [
    { "date": "2026-10-05", "cabins": { "economy": { "miles": 42500, "seats": 4, "direct": false }, "business": { "miles": 88000, "seats": 2, "direct": false } } },
    { "date": "2026-10-06", "cabins": { "business": { "miles": 85000, "seats": 1, "direct": true } } },
    { "date": "2026-10-07", "cabins": { "economy": { "miles": 40000, "seats": 6, "direct": true }, "business": { "miles": 92000, "seats": 3, "direct": false } } },
    { "date": "2026-10-08", "cabins": { "economy": { "miles": 44000, "seats": 2, "direct": false } } }
  ]
}
```

**Composition:** built zero-quota from `cache query`, so no seats.aero credits are spent. Each populated cell is a `{date, cabin}` the human can toggle, and the accumulated picks stream back on submit.

## getaway.stay

One journey's lodging. A `state` discriminator picks the shape: `"searched"` carries the rooms.aero walk for a paired journey; `"deferred"` names why a journey has no lodging to show.

Stay cash is per-night minor units in each room's own `currency` (`cashPerNightCents` plus the room `currency`), not a self-contained money object, because a property quotes one local currency across all its offers.

**Interaction:** `{note}` (required, ≤ 2000 chars) in both states. Note-only.

### state `"searched"`

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.stay"` | yes | The dotted wire type. |
| `state` | `"searched"` | yes | The walked shape. |
| `destination` | string | yes | Header context, the stay's `destination.query`. |
| `airport` | IATA | no | Chip beside the city. |
| `session` | `"pro"` \| `"anonymous"` | yes | `anonymous` renders a prominent staleness banner — that data can be weeks old. |
| `checkedAt` | UTC instant | yes | Entry-level freshness; renders as relative age. |
| `searchState` | enum | yes | See below. |
| `interval` | interval | yes | Check-in through check-out. |
| `rooms` | room[], min 0 | yes | Empty is valid — a `searched_empty` walk found nothing. |

`searchState` is one of `complete`, `searched_empty`, `night_clamped`, `bot_wall`, `logged_out`, `date_in_past`, `geocode_miss`, `failed`. With rooms present the block renders them; with `rooms` empty and `searched_empty` it renders "No award rooms found"; with `rooms` empty and any of `bot_wall`, `logged_out`, `date_in_past`, `geocode_miss`, or `failed` it renders an honest "lookup couldn't complete" notice — a failed walk never reads as "no space".

Each interval:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `checkIn`, `checkOut` | `YYYY-MM-DD` | yes | Destination-local dates. |
| `nights` | integer ≥ 1 | yes | `checkOut − checkIn`. |
| `nightClamped` | boolean | yes | `true` discloses rooms.aero's 5-night booking cap. |
| `requestedNights` | integer ≥ 6 | no | Pre-clamp nights. Present renders "first 5 nights of N"; omit and the disclosure reads "capped at rooms.aero's 5-night maximum". |

Each room:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `program` | string | yes | Hotel program display name, such as "World of Hyatt". |
| `name` | string | yes | Property name. |
| `currency` | ISO 4217 | yes | Property-local; every cash figure in this room is in it. |
| `checkedAt` | UTC instant | yes | The row's `last_checked_at`; per-room freshness. |
| `stale` | boolean | yes | `true` shows a `stale` warning chip. |
| `offers` | offer[], min 1 | yes | One per award class. |

Each offer:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `awardClass` | `"standard"` \| `"suite"` | yes | |
| `pointsPerNight` | integer ≥ 1 \| null | yes | `null` on a cash-only offer. |
| `cashPerNightCents` | integer ≥ 0 \| null | yes | Minor units in the room's `currency`; `null` on a points-only offer. |
| `centsPerPoint` | number > 0 \| null | yes | Value ratio; renders as a `¢/pt` chip when present. |

At least one of `pointsPerNight` and `cashPerNightCents` must be non-null; the schema rejects an offer that is both. Per-night figures are the source of truth. The block prints a per-offer estimate (`per-night × nights`) labeled "est." and a footer saying so, so no total ever reads as a quote.

**Example** — `examples/stay.json`:

```json
{
  "id": "stay-jrn-sfo-lis-hyatt",
  "type": "getaway.stay",
  "state": "searched",
  "destination": "Lisbon",
  "airport": "LIS",
  "session": "pro",
  "checkedAt": "2026-07-13T14:20:00Z",
  "searchState": "complete",
  "interval": { "checkIn": "2026-09-06", "checkOut": "2026-09-11", "nights": 5, "nightClamped": false },
  "rooms": [
    {
      "program": "World of Hyatt",
      "name": "Hyatt Regency Lisbon",
      "currency": "EUR",
      "checkedAt": "2026-07-13T14:20:00Z",
      "stale": false,
      "offers": [
        { "awardClass": "standard", "pointsPerNight": 12000, "cashPerNightCents": 18500, "centsPerPoint": 1.54 },
        { "awardClass": "suite", "pointsPerNight": 24000, "cashPerNightCents": 41000, "centsPerPoint": 1.71 }
      ]
    },
    {
      "program": "Hilton Honors",
      "name": "Hilton Lisbon",
      "currency": "EUR",
      "checkedAt": "2026-06-28T09:00:00Z",
      "stale": true,
      "offers": [
        { "awardClass": "standard", "pointsPerNight": 70000, "cashPerNightCents": null, "centsPerPoint": null },
        { "awardClass": "suite", "pointsPerNight": null, "cashPerNightCents": 52000, "centsPerPoint": null }
      ]
    }
  ]
}
```

### state `"deferred"`

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.stay"` | yes | The dotted wire type. |
| `state` | `"deferred"` | yes | The no-lodging shape. |
| `reason` | enum | yes | `no_checkout`, `open_jaw_stop`, `date_in_past`, `invalid_interval`, or `not_walked`. |
| `destination` | string | no | Optional context header. |
| `airport` | IATA | no | Chip beside the city. |

The block renders an honest one-line reason per code: `no_checkout` (no confirmed return date), `open_jaw_stop` (an intermediate stop whose onward flight departs a different airport, so no checkout date anchors a lodging search), `date_in_past`, `invalid_interval`, or `not_walked` (a walk gap, never "no space").

**Example** — `examples/stay-deferred.json`:

```json
{
  "id": "stay-jrn-sfo-lis-lead",
  "type": "getaway.stay",
  "state": "deferred",
  "reason": "no_checkout",
  "destination": "Lisbon",
  "airport": "LIS"
}
```

### Composing a stay block

The board threads one lodging disposition onto each journey. Map it to one `getaway.stay` block:

- A `stays.json` entry becomes a `"searched"` block. Copy `provenance.session` into `session`, `provenance.fetched_at` into `checkedAt`, `provenance.night_clamped` into `interval.nightClamped`, the entry `search_state` into `searchState`, and `destination.query` into `destination`. Per room, copy `last_checked_at` into the room's `checkedAt` and resolve the registry slug to a program display name. When the interval clamped, pass the journey's true nights as `requestedNights` so the disclosure can name N.
- A `lodging_search: {state: "deferred", reason}` becomes a `"deferred"` block carrying that `reason`.
- A walk gap, `{state: "unavailable", reason: "not_walked"}`, becomes a `"deferred"` block with `reason: "not_walked"`.

## getaway.booking

The final booking sheet once a trip is locked — every link, transfer, tax, and seat pick needed to book. Fed from the locked journey at booking time, with legs in chain order.

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.booking"` | yes | The dotted wire type. |
| `title` | string | yes | Sheet heading. |
| `subtitle` | string | no | One-line routing summary. |
| `fetchedAt` | UTC instant | yes | When the trip was priced; renders as relative age. |
| `totals` | totals | no | Rolled-up miles and cash. |
| `transfers` | transfer[] | no | Ordered transfer-first checklist. |
| `legs` | leg[], min 1 | yes | Chain order. |

`totals` (when present, both arrays required):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `miles` | `{program, miles}[]` | yes | One entry per program drawn on. |
| `cash` | money[] | yes | One entry per currency; never cross-summed. |

Each transfer (`transfers[]`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `from` | string | yes | Source program. |
| `to` | string | yes | Destination program. |
| `amount` | integer ≥ 1 | yes | Points to move. |
| `note` | string | no | Timing caveat. |

Each leg (`legs[]`):

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `role` | string | yes | Free-form label, such as "Positioning" or "Long-haul award". |
| `kind` | `"award"` \| `"cash"` | yes | Award requires `program` and `miles`; cash requires `price`. |
| `program` | string | award only | Mileage program. |
| `miles` | integer ≥ 1 | award only | Award cost. |
| `taxes` | money[] | no | Per-currency tax lines. |
| `taxesNote` | string | no | Prose when taxes lack a currency. |
| `price` | money | cash only | Cash fare. |
| `flights` | flight[], min 1 | yes | Legs render in order. |
| `bookingLinks` | bookingLink[], min 1 | yes | One is primary. |
| `notes` | string[] | no | Per-leg booking cautions. |

Each flight (`legs[].flights[]`) mirrors `getaway.flight` with one difference: `cabin` is optional here. Required are `flightNumber`, `origin`, `destination`, `departsAt`, `arrivesAt`, and `durationMinutes`; `cabin`, `aircraft`, `aircraftCode`, `operatedBy`, and `seat` are optional. `operatedBy` is `{carrier, name}`, both required — the operating airline on a codeshare, copied verbatim from the advice row's `operated_by`; it renders dim beside the aircraft as "operated by {name}". The `seat` object:

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `verdict` | `suite` \| `solid` \| `dated` \| `barely` \| `verify` | yes | Seat quality tier. |
| `product` | string \| null | no | Named product. |
| `note` | string \| null | no | Seat advice. |
| `picks` | `{seat, why}[]` | no | Seats to grab. |
| `avoids` | `{seat, why}[]` | no | Seats to skip. |

Each `picks` and `avoids` entry is `{seat, why}`: `seat` is the seat number and `why` a one-line reason, both required.

`role` is a free string: v3 journeys are arbitrary chains, so "outbound", "onward", "return", and "positioning" are conventions, not an enum.

**Interaction:** `{note}` (required, ≤ 2000 chars). Note-only.

**Example** — `examples/booking.json`:

```json
{
  "id": "booking-lax-cpt-qr",
  "type": "getaway.booking",
  "title": "Book Los Angeles to Cape Town",
  "subtitle": "Position to San Francisco, then fly Qatar Airways business class via Doha",
  "fetchedAt": "2026-07-11T02:10:00Z",
  "totals": {
    "miles": [{ "program": "Air Canada Aeroplan", "miles": 88000 }],
    "cash": [{ "amount": 42550, "currency": "USD" }, { "amount": 37500, "currency": "ZAR" }]
  },
  "transfers": [
    { "from": "Chase Ultimate Rewards", "to": "Air Canada Aeroplan", "amount": 88000, "note": "Transfer only after the award space is visible in Aeroplan." }
  ],
  "legs": [
    {
      "role": "Positioning",
      "kind": "cash",
      "price": { "amount": 30500, "currency": "USD" },
      "flights": [
        { "flightNumber": "UA1682", "origin": "LAX", "destination": "SFO", "departsAt": "2026-09-06T12:15", "arrivesAt": "2026-09-06T13:45", "cabin": "economy", "durationMinutes": 90 }
      ],
      "bookingLinks": [
        { "label": "Book the positioning flight on United", "url": "https://www.united.com/en/us/book-flight/united-reservations", "primary": true }
      ],
      "notes": ["Allow at least three hours in San Francisco because this is a separate ticket."]
    },
    {
      "role": "Long-haul award",
      "kind": "award",
      "program": "Air Canada Aeroplan",
      "miles": 88000,
      "taxes": [{ "amount": 12050, "currency": "USD" }, { "amount": 37500, "currency": "ZAR" }],
      "flights": [
        {
          "flightNumber": "QR738", "origin": "SFO", "destination": "DOH",
          "departsAt": "2026-09-06T17:30", "arrivesAt": "2026-09-07T19:45",
          "cabin": "business", "durationMinutes": 975, "aircraft": "Boeing 777-300ER", "aircraftCode": "77W",
          "seat": { "verdict": "suite", "product": "Qsuite", "note": "Odd-numbered window suites face the window.", "picks": [{ "seat": "11A", "why": "True window, closes fully." }, { "seat": "11K", "why": "True window, closes fully." }], "avoids": [{ "seat": "10E", "why": "Center pair is less private for a solo traveler." }, { "seat": "10F", "why": "Center pair is less private for a solo traveler." }] }
        },
        {
          "flightNumber": "QR1369", "origin": "DOH", "destination": "CPT",
          "departsAt": "2026-09-07T21:50", "arrivesAt": "2026-09-08T06:05",
          "cabin": "business", "durationMinutes": 555, "aircraft": "Airbus A350-900", "aircraftCode": "359",
          "seat": { "verdict": "suite", "product": "Qsuite", "note": "Confirm the seat map after ticketing in case of an aircraft swap.", "picks": [{ "seat": "3A", "why": "True window, closes fully." }, { "seat": "3K", "why": "True window, closes fully." }], "avoids": [{ "seat": "1E", "why": "Close to the galley and bassinets." }, { "seat": "1F", "why": "Close to the galley and bassinets." }] }
        }
      ],
      "bookingLinks": [
        { "label": "Book the award on Air Canada", "url": "https://www.aircanada.com/us/en/aco/home/book.html", "primary": true },
        { "label": "Choose seats on Qatar Airways", "url": "https://booking.qatarairways.com/nsp/views/retrievePnr.xhtml", "primary": false }
      ],
      "notes": ["Search and ticket both Qatar Airways flights as one Aeroplan award."]
    }
  ]
}
```

**Composition** — from the locked journey:

- `legs` in chain order. An award leg carries `program` and `miles`; a cash leg carries `price`. The schema enforces this through an `if`/`then` on `kind`.
- `seat` per flight merges the registry verdict with the live `seat_advice` picks and avoids. The `picks` and `avoids` arrays pass the CLI's `{seat, why}` objects straight through — no reshaping. The itinerary block shows the verdict alone; the booking sheet adds these live seats.
- A cash leg's flights come from the cash quote, which carries only the first segment's `flight_number` plus aggregate departure, arrival, and duration. Build one flight row from exactly that — `flightNumber` from the first segment, `departsAt`/`arrivesAt`/`durationMinutes` from the aggregate — and name any connections in the leg's `notes`. Do not synthesize per-segment flight numbers the quote lacks, and do not drop the required `flightNumber`.
- A cash leg omits `cabin` when the quote carries none; award flights always set it.
- Cash legs have no CLI `booking_links`. The composer authors `bookingLinks` for them — an honestly labeled deep link such as Google Flights — never inventing a carrier's own booking URL.
- `transfers` is the ordered transfer-first checklist from `afford`.
- `totals.cash` is per-currency and never cross-summed.

## Shortlists

A shortlist of finalists uses cc-present's built-in `choice` block; the pack ships no option picker of its own. Give the block `multi: true`, each option a `facts[]` array for aligned columns, and each option a `detail` for pros and cons.
