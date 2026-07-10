# seats.aero Partner API reference

Working notes for building against the seats.aero Partner API, distilled from
the [official docs](https://developers.seats.aero/) as of July 2026. The docs
embed per-endpoint OpenAPI 3.1 specs and publish a machine-readable index at
[developers.seats.aero/llms.txt](https://developers.seats.aero/llms.txt).
There is no official Python client; the API is raw REST.

## Access and auth

There is one API with two tiers. A Pro subscription, about $10/mo, includes
API access for personal, non-commercial use. Generate a key on the seats.aero
Settings page under the API tab. The tab is gated by geography and account
factors, and an account with no tab has no API access. Commercial access
requires a written agreement via support@seats.aero.

Keys start with `pro_` and go in the `Partner-Authorization` header, not
`Authorization`, on every request. The base URL is
`https://seats.aero/partnerapi`.

Pro keys get 1,000 calls per calendar day, resetting at midnight UTC.
Remaining quota comes back in the `X-RateLimit-Remaining` header; at zero,
requests are rejected until reset. Failed live searches don't count against
quota.

Live Search is commercial-only. Pro keys cannot use `POST /live` at all, so
plan around cached data.

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/search` | GET | Cached search between specific airports/dates across all programs; the workhorse |
| `/availability` | GET | Bulk availability for one program; `source` required |
| `/trips/{id}` | GET | Flight-level detail for one availability object's `ID`: segments, flight numbers, seats, miles and taxes, booking links |
| `/routes` | GET | Routes monitored for a given `source`; `source` required |
| `/live` | POST | Real-time airline query; commercial keys only, 5–15s responses, returned IDs are ephemeral |
| `/consent`, `/token`, `/userinfo` | — | OAuth flow for "Login with Seats.aero" |

### Cached search params (`GET /search`)

- `origin_airport`, `destination_airport`: required, comma-delimited lists
  like `SFO,LAX`
- `start_date`, `end_date`: `YYYY-MM-DD`
- `cabins`: comma-delimited from `economy,premium,business,first`
- `sources`: comma-delimited program filter like `aeroplan,united`
- `take`: 10–1000, default 500. `skip` plus `cursor` paginate. Pass the first
  response's `cursor` back verbatim; it looks like a Unix timestamp but treat
  it as opaque, and dedupe pages by `ID`.
- `order_by=lowest_mileage`: alternative to the default date ordering
- `only_direct_flights`, `carriers` like `DL,AA`, `include_trips` with
  optional `minify_trips`, `include_filtered`

### Bulk availability params (`GET /availability`)

`source` is required. Optional: `cabin`, `start_date`/`end_date`,
`origin_region`/`destination_region` covering six continents,
`take`/`cursor`/`skip`, `include_filtered`.

## Data shapes

Responses carry a `data` array of Availability objects, one per
route+date+program, with an embedded `Route` and an `UpdatedAt` timestamp.
Cabin-specific fields are keyed `Y`/`W`/`J`/`F`: `YAvailable`,
`YMileageCost`, `YRemainingSeats`, `YAirlines`, `YDirect`, and so on.

Cached data is crawler-populated, roughly every few hours per route, so
snapshots range from minutes to a couple of days old. Always check
`UpdatedAt`.

"Dynamic price filtering" hides expensive dynamically-priced awards by
default on both cached and live results. Override with `include_filtered` on
cached endpoints or `disable_filters` on live.

Type quirks to handle: `MileageCost` is a string in Availability objects but
an integer in trips. Taxes are integers in minor currency units with a
`TaxesCurrency` field. Some programs report seat counts of `0` or return no
trip data.

## Sources (mileage programs)

26 as of July 2026: `aeroplan`, `united`, `american`, `delta`, `alaska`,
`flyingblue`, `lufthansa`, `singapore`, `qatar`, `turkish`, `emirates`,
`etihad`, `qantas`, `velocity`, `virginatlantic`, `jetblue`, `finnair`,
`eurobonus`, `aeromexico`, `connectmiles`, `azul`, `smiles`, `ethiopian`,
`saudia`, `frontier`, `spirit`.

Coverage varies per program; the table on the docs
[Concepts page](https://developers.seats.aero/reference/concepts-copy) has
the details. For example `american`, `qantas`, and `emirates` lack reliable
seat counts, while `qatar`, `turkish`, and `singapore` lack taxes.

## Sources consulted

- https://developers.seats.aero/reference/getting-started-p
- https://developers.seats.aero/reference/concepts-copy
- https://developers.seats.aero/reference/cached-search
- https://developers.seats.aero/reference/get-availability
- https://developers.seats.aero/reference/get-trips
- https://developers.seats.aero/reference/live-search
- https://docs.seats.aero/article/68-seatsaero-pro-api-access-limits-and-usage
