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
- `take`: 10–1000, default 500. `skip` plus `cursor` paginate; see
  Pagination below.
- `order_by=lowest_mileage`: alternative to the default date ordering
- `only_direct_flights`, `carriers` like `DL,AA`, `include_trips` with
  optional `minify_trips`, `include_filtered`

### Bulk availability params (`GET /availability`)

`source` is required. Optional: `cabin`, `start_date`/`end_date`,
`origin_region`/`destination_region`, `take`/`cursor`/`skip`,
`include_filtered`.

`origin_region` and `destination_region` take one of six continent names:
`Africa`, `Asia`, `Europe`, `North America`, `Oceania`, `South America`.
URL-encode the spaces (`North%20America`). The region assignment is the
API's own classification, not strict geography: `destination_region=Africa`
returns Indian Ocean airports (MRU, MLE) and Canary Islands airports
(FUE, ACE) alongside continental Africa (observed 2026-07-10, `aeroplan`).

### Routes (`GET /routes`)

`source` is required and is the only parameter; there is no server-side
region filter. The response is a bare JSON array — no `data` envelope and
no pagination fields (observed 2026-07-12, `aeroplan`) — so the jq root is
`.[]`, not `.data[]`. One call returns the program's entire monitored route
set (8,260 rows for `aeroplan`) and counts against quota. Dumps run
thousands of lines: always redirect to a scratchpad file.

Each route object carries seven fields:

```json
{"ID":"2OgDZKKJO8xO0Gd67UogPLwJR6G","OriginAirport":"CAI","OriginRegion":"Africa","DestinationAirport":"AMM","DestinationRegion":"Asia","Distance":293,"Source":"aeroplan"}
```

`OriginRegion` and `DestinationRegion` take the same six continent names as
the availability region params, so region cuts are a client-side jq filter
(`OriginRegion == "Asia"` matched 2,040 of the 8,260 aeroplan rows).
`Distance` is the route's great-circle length in statute miles (293 for
CAI–AMM), a free duration proxy for the cash-cabin cutoff that spends no
`/trips` call.

### Pagination

Paginated responses carry three top-level continuation fields (observed
2026-07-10 on `/search`):

| Field | Type | Meaning |
|---|---|---|
| `cursor` | integer | Pass back verbatim as `cursor=` for the next page; null or absent on the last page. Unix-timestamp-shaped but opaque. |
| `hasMore` | boolean | `true` while another page exists; `false` on the last page. |
| `moreURL` | string | Ready-made next-page path plus query. Carries both `skip=<rows so far>` and `cursor=<cursor>`. |
| `count` | integer | Rows in this page's `data`. |

A next-page request sends both `skip` and `cursor`, matching `moreURL`.
Dedupe rows across pages by `ID`. There is no snake_case `has_more` field,
and `skip`/`take` are not echoed at the top level.

## Region pseudo-codes

`origin_airport` and `destination_airport` on `/search` accept region
pseudo-codes in addition to IATA airport codes. Verified 2026-07-10: the
Partner API accepts pseudo-codes and expands them server-side, and the
expansion is a superset of the UI-documented airport list — a `WST` origin
returned SFO, SEA, LAX, YVR, LAS, PHX, PDX, SAN, and SLC (the UI docs list
eight airports without LAX, SEA, or PHX), and an `ASA` destination returned
NRT, ICN, HND, TPE, PVG, HKG, BKK, and SIN. Treat the airport lists below as
the documented floor, not the exact expansion.

An Africa pseudo-code exists but is undocumented: `QAF` works on
`/search` (verified live 2026-07-12) despite having no row in the
knowledge-base table below — it extends the Q-prefix family of the
documented `QBA`, `QLA`, and `QMI` metro codes. A 100-row probe
expanded it to CMN, CAI, ADD, CPT, JNB, and NBO — six airports in five
countries; treat that as a floor, like the lists below.

The full UI-documented list, from the
[seats.aero knowledge base](https://docs.seats.aero/article/36-how-to-search-by-airport-city-or-region-code)
(last updated August 2025):

| Code | Name | Airports included |
|---|---|---|
| AAH | American Airlines – major hubs | MIA, DFW, PHX, CLT, PHL, JFK, GRD |
| ANZ | Australia & New Zealand – large airports | SYD, MEL, BNE, PER, AKL, ADL |
| ASA | Asia – large airports | HND, NRT, SIN, BKK, ICN, HKG, KUL, TPE, PVG, PEK, PNK |
| AUL | Australia – large airports | SYD, MEL, BNE, PER, ADL |
| BJS | Beijing metropolitan area | PEK, PKX |
| BRL | Brazil – large airports | GRU, GIG, CNF, BSB, REC, POA, FLN, CWB, FOR, MAO, BEL, VCP, CGB, NAT, SLZ, MEZ, AJU, JPA, IGU, THE, CPV, PVH, PMR, JDO, LDB, SJP, CGR, IOS, PMW, STM, MAD |
| CAD | Canada – large airports | YVR, YYZ, YYC, YUL, YEG, YOW, YHZ, YQB, YQR, YXE |
| CAL | California, United States | LAX, SFO, SAN, OAK, SJC, SMF |
| CAR | Caribbean – large airports | AUA, BGI, BON, ANU, AXA, SJU, STX, SXM |
| CHI | Chicago metropolitan area | ORD, MDW |
| CNA | Mainland China – large airports | PEK, PVG, CAN, SZX, CSX, TSN, XMN |
| DLL | Delta Air Lines – major hubs | ATL, DTW, MSP, SEA, SLC, LAX, JFK, BOS |
| EST | East Coast, United States | JFK, LGA, EWR, BOS, PHL, PIT, IAD, DCA, CLT |
| EUR | Europe – large airports | AMS, ATH, BCN, BER, CDG, CPH, DUB, FRA, FIS, LHR, LIS, MAD, FCO, ZRH, HEL, ARN, VIE, BRU, PRG |
| GCR | Germany – large airports | MUC, FRA, BER |
| JPN | Japan – large airports | HND, NRT, KIX, NGO |
| LON | London metropolitan area | LHR, LGW, STN, LTN |
| MEA | Middle East – large airports | DXB, AUH, DOH |
| MEX | Mexico – large airports | MEX, CUN, GDL, MTY, TIJ, SJD, PVR |
| MMW | Midwest, United States | ORD, MSP, DTW, CLE, CVG, IND, MKE |
| NYC | New York City metropolitan area | JFK, LGA, EWR |
| OSA | Osaka metropolitan area | KIX, ITM |
| PAR | Paris metropolitan area | CDG, ORY |
| QBA | San Francisco Bay Area | SFO, SJC, OAK |
| QLA | Los Angeles metropolitan area | LAX, BUR, SNA, ONT, LGB |
| QMI | Miami metropolitan area | MIA, FLL, PBI |
| RIO | Rio de Janeiro metropolitan area | GIG, SDU |
| SAM | South America – large airports | EZE, GRU, GIG, SCL, LIM, BOG |
| SAO | São Paulo metropolitan area | GRU, CGH, VCP |
| SCH | Schengen Area – large airports | AMS, ATH, BCN, BER, CDG, FRA, LIS, MAD, FCO, ZRH, HEL, ARN, VIE, BRU, CPH, PRG, AGP |
| SEA | Southeast Asia – large airports | SIN, KUL, BKK, SGN, HAN, MNL, CGK, DPS |
| SEL | Seoul metropolitan area | ICN, GMP |
| TYO | Tokyo metropolitan area | HND, NRT |
| UAH | United Airlines – major hubs | DEN, IAH, ORD, SFO, LAX, EWR, IAD |
| UKD | United Kingdom – large airports | LHR, LGW, MAN |
| USA | United States – large airports | SFO, LAX, JFK, EWR, ORD, ATL, IAD, DFW, MIA, SEA, DEN, BOS |
| WAS | Washington, DC metropolitan area | IAD, DCA, BWI |
| WST | West Coast, United States | SFO, SJC, SAN, PDX, DEN, YVR, LAS, SLC |
| YTO | Toronto metropolitan area | YYZ, YTZ |

The `SEA` pseudo-code (Southeast Asia) collides with SEA the Seattle airport
code; the search UI disambiguates via its dropdown, and the Partner API's
resolution of the bare string is undocumented.

## Data shapes

Responses carry a `data` array of Availability objects, one per
route+date+program, with an embedded `Route` and an `UpdatedAt` timestamp.
Cabin-specific fields are keyed `Y`/`W`/`J`/`F`: `YAvailable`,
`YMileageCost`, `YRemainingSeats`, `YAirlines`, `YDirect`, and so on.
Cabin airline fields such as `JAirlines` hold comma-joined IATA carrier
codes (`"AF, DL"`), not airline names.

### Trip responses (`GET /trips/{id}`)

The response envelope (observed 2026-07-10) has five top-level keys:

| Key | Type | Contents |
|---|---|---|
| `data` | array | Trip objects, one per bookable itinerary under the availability |
| `booking_links` | array | `{label, link, primary}` objects; booking links live here, not on trips |
| `carriers` | object | Map of IATA code to airline name for every carrier in `data` |
| `origin_coordinates` | object | `{Lat, Lon}` |
| `destination_coordinates` | object | `{Lat, Lon}` |

Each trip in `data` carries `MileageCost` (an integer here, e.g. `44000`; a
string in `/search` rows), `TotalTaxes` (integer, minor currency units,
e.g. `18560`) with `TaxesCurrency` (`"USD"`), `RemainingSeats`, `Stops`,
`Connections`, `FlightNumbers`, `FareClasses`, `Carriers`, `TotalDuration`,
and an `AvailabilitySegments` array. Each segment has `FlightNumber`,
`OriginAirport`, `DestinationAirport`, `AircraftName`, `AircraftCode`,
`Cabin`, `FareClass`, `DepartsAt`, `ArrivesAt`, `Duration`, `Distance`, and
`Order`.

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

26 documented as of July 2026: `aeroplan`, `united`, `american`, `delta`,
`alaska`, `flyingblue`, `lufthansa`, `singapore`, `qatar`, `turkish`,
`emirates`, `etihad`, `qantas`, `velocity`, `virginatlantic`, `jetblue`,
`finnair`, `eurobonus`, `aeromexico`, `connectmiles`, `azul`, `smiles`,
`ethiopian`, `saudia`, `frontier`, `spirit`.

Beta sources ship ahead of the documentation: `british` (BA Club,
launched ~May 2026) returns live `/search` rows (observed 2026-07-12)
while missing from the Concepts table above; `iberia` reportedly
shipped in the same beta but is unconfirmed in API responses.

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
