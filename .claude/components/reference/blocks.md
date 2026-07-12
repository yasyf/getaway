# getaway pack blocks

Four block types under the `getaway` pack. Reference them by dotted wire
type inside any `Doc.blocks` array or a card's `children`.

Conventions shared by every block:

- Money fields are `{amount, currency}` objects: `amount` is an integer in
  minor currency units, `currency` an ISO 4217 code. `18560` + `USD`
  renders as $185.60. `fli` returns major-unit floats — convert before
  composing (`305.0` USD becomes `{"amount": 30500, "currency": "USD"}`).
- `departsAt` and `arrivesAt` are local wall-clock timestamps rendered
  verbatim, never timezone-converted. A trailing `Z` from seats.aero is
  accepted and ignored. Arrival on a different local date renders a day
  suffix: `+1`, `+2`, or `-1` for an eastbound dateline crossing.
- Cabin values are `economy`, `premium`, `business`, or `first`.
- Airports are uppercase IATA codes; durations are integer minutes.

## getaway.itinerary

One bookable award option with its full segment list. Content only, no
interaction. Feed it from `/trips/{id}` — integer `MileageCost`,
`TotalTaxes` plus `TaxesCurrency`, the primary `booking_links` entry, and
the availability row's `UpdatedAt` — never from `/search` rows, whose
`MileageCost` is a string.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.itinerary"` | yes | The dotted wire type. |
| `program` | string | yes | Mileage program display name ("Aeroplan"). |
| `miles` | integer ≥ 1 | yes | Integer mileage cost from the trip object. |
| `taxes` | money | yes | Minor units plus currency. |
| `remainingSeats` | integer ≥ 0 | yes | Shown in the meta line. |
| `bookingLink` | `{label, url}` | yes | `url` must start `https://`; opens in a new tab. |
| `updatedAt` | timestamp with offset | yes | The row's `UpdatedAt`; renders as relative freshness ("6 hours ago"). |
| `totalDurationMinutes` | integer ≥ 1 | yes | The trip's `TotalDuration`. |
| `segments` | segment[], min 1 | yes | Pre-sorted by `Order`; the block renders array order. |

Each segment:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `flightNumber` | string | yes | Carrier code plus number, like `QR738`. |
| `origin`, `destination` | IATA | yes | |
| `departsAt`, `arrivesAt` | local wall clock | yes | |
| `cabin` | cabin | yes | |
| `aircraft` | string | yes | seats.aero `AircraftName`. |
| `durationMinutes` | integer ≥ 1 | yes | |

The header route derives from the first segment's `origin` and the last
segment's `destination`. A layover divider with the computed gap appears
between segments that share an airport (`prev.destination` equals
`next.origin`); an open jaw renders a plain divider.

```json
{
  "id": "itin-sfo-cpt-qr",
  "type": "getaway.itinerary",
  "program": "Aeroplan",
  "miles": 88000,
  "taxes": { "amount": 12050, "currency": "USD" },
  "remainingSeats": 3,
  "bookingLink": { "label": "Book on Air Canada", "url": "https://www.aircanada.com/us/en/aco/home/book.html" },
  "updatedAt": "2026-07-11T02:10:00Z",
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
      "durationMinutes": 555
    }
  ]
}
```

## getaway.flight

A single leg. Content only, no interaction. The fields match an itinerary
segment, with two differences: `aircraft` is optional, and an optional
`price` (money) marks the leg as a cash positioning flight.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.flight"` | yes | The dotted wire type. |
| `flightNumber` | string | yes | Carrier code plus number. |
| `origin`, `destination` | IATA | yes | |
| `departsAt`, `arrivesAt` | local wall clock | yes | |
| `cabin` | cabin | yes | |
| `durationMinutes` | integer ≥ 1 | yes | |
| `aircraft` | string | no | Omit when the source has none. |
| `price` | money | no | Present on a cash positioning leg only. |

An award leg:

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
  "aircraft": "Boeing 777-300ER"
}
```

A positioning leg:

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

## getaway.availability

A date × cabin mileage grid for one market, built from saved sweep JSONL.
Interactive: tapping a populated cell streams back a `pack.interaction`
with payload `{"date": "<YYYY-MM-DD>", "cabin": "<cabin>"}` — the user is
asking to expand that cell with `trip`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.availability"` | yes | The dotted wire type. |
| `origin`, `destination` | IATA | yes | One market per grid. |
| `program` | string | no | Header badge for single-program sweeps; omit when mixed. |
| `rows` | row[], min 1 | yes | One row per date. |

Each row is `{date, cabins}`: `date` is `YYYY-MM-DD`, `cabins` maps cabin
names to cells and needs at least one entry. An absent cabin key means no
space that day and renders as a dash, not a button. Each cell:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `miles` | integer ≥ 1 | yes | Cheapest award at that date and cabin. |
| `seats` | integer ≥ 0 | yes | Remaining seats. |
| `direct` | boolean | yes | `true` renders a nonstop tag. |

```json
{
  "id": "avail-sfo-fra",
  "type": "getaway.availability",
  "origin": "SFO",
  "destination": "FRA",
  "program": "Aeroplan",
  "rows": [
    {
      "date": "2026-10-05",
      "cabins": {
        "economy": { "miles": 42500, "seats": 4, "direct": false },
        "business": { "miles": 88000, "seats": 2, "direct": false }
      }
    },
    {
      "date": "2026-10-06",
      "cabins": {
        "business": { "miles": 85000, "seats": 1, "direct": true }
      }
    },
    {
      "date": "2026-10-07",
      "cabins": {
        "economy": { "miles": 40000, "seats": 6, "direct": true },
        "business": { "miles": 92000, "seats": 3, "direct": false }
      }
    },
    {
      "date": "2026-10-08",
      "cabins": {
        "economy": { "miles": 44000, "seats": 2, "direct": false }
      }
    }
  ]
}
```

## getaway.option-picker

A shortlist the user picks from. Interactive: tapping an option streams
back a `pack.interaction` with payload `{"optionId": "<id>"}` — that
option is the chosen finalist.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"getaway.option-picker"` | yes | The dotted wire type. |
| `label` | string | yes | Prompt shown above the options. |
| `options` | option[], min 1 | yes | |

Each option:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `optionId` | string | yes | Your handle for the pick — use the availability row's `ID`. Must be unique within the block; the schema cannot enforce this. |
| `origin`, `destination` | IATA | yes | |
| `date` | `YYYY-MM-DD` | yes | Departure date. |
| `program` | string | yes | Display name. |
| `miles` | integer ≥ 1 | yes | |
| `taxes` | money | yes | |
| `cabin` | cabin | no | Chip shown when options span cabins. |

```json
{
  "id": "opt-fall-europe",
  "type": "getaway.option-picker",
  "label": "Which outbound should I book?",
  "options": [
    {
      "optionId": "opt-sfo-cpt-qr",
      "origin": "SFO",
      "destination": "CPT",
      "date": "2026-09-06",
      "program": "Aeroplan",
      "miles": 88000,
      "taxes": { "amount": 12050, "currency": "USD" },
      "cabin": "business"
    },
    {
      "optionId": "opt-sfo-ath-lh",
      "origin": "SFO",
      "destination": "ATH",
      "date": "2026-10-05",
      "program": "Lufthansa Miles & More",
      "miles": 84000,
      "taxes": { "amount": 8890, "currency": "USD" }
    },
    {
      "optionId": "opt-sfo-lis-ba",
      "origin": "SFO",
      "destination": "LIS",
      "date": "2026-10-12",
      "program": "British Airways Club",
      "miles": 76000,
      "taxes": { "amount": 15600, "currency": "USD" }
    }
  ]
}
```
