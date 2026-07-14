# SerpApi Google Flights API reference

Working notes for the SerpApi Google Flights engine as getaway's fallback
cash-fare backend, distilled from the
[official docs](https://serpapi.com/google-flights-api) and a research
sweep on 2026-07-13. The API is raw REST returning JSON; getaway calls it
directly, skipping SerpApi's client libraries.

SerpApi sits behind `getaway bridge`'s search seam
(`cli/getaway/serp.py`): the fli library (Google Flights) prices cash legs
first, and SerpApi engages per airport pair only when fli errors or
returns zero priced results and a key resolves. Every quote records its
backend as `source: fli` or `source: serpapi`.

## Access and auth

Signup is self-serve with no KYC. The free tier is 250 searches per month,
recurring, capped at 50 per hour; only successful searches count against
the quota. The Starter plan is $25/mo for 1,000 searches. The Google
Flights engine is included on every plan, the free tier included (verified
2026-07-13 — the least-corroborated fact in the research sweep; if signup
shows engine gating, the Starter plan is the fallback posture).

The key rides as the `api_key` query parameter, not a header. getaway's
error paths therefore strip query strings: a failure message names the
bare `https://serpapi.com/search` endpoint and the status code with the
URL's params dropped, and tests pin that the key cannot leak into a
message or rendered traceback. Key resolution follows the shared `keys.resolve` order: the
`SERPAPI_API_KEY` env var wins, else the `serpapi_op_ref` preferences key,
a 1Password `op://` reference read via `op read`.

## Request

One endpoint: `GET https://serpapi.com/search`. The parameters getaway
sends:

| Param | Value |
|---|---|
| `engine` | `google_flights` |
| `departure_id` | Origin IATA code |
| `arrival_id` | Destination IATA code |
| `outbound_date` | `YYYY-MM-DD` |
| `type` | `2` (one-way) |
| `currency` | `USD` |
| `travel_class` | `1` economy, `2` premium economy, `3` business, `4` first |
| `api_key` | The key, as a query param |

## Response shapes

Priced options arrive in two top-level arrays, `best_flights` and
`other_flights` — either may be absent (observed via review 2026-07-14).
getaway merges both arrays, drops options without a `price`, and picks the
cheapest.

Each option carries:

- `flights`: the segment array. Each segment has `departure_airport` and
  `arrival_airport` objects (`{name, id, time}`, where `time` is the
  airport's local wall clock as `"YYYY-MM-DD HH:MM"`), `airline`,
  `flight_number`, and `plane_and_crew_by` on codeshares.
- `total_duration`: minutes.
- `layovers`: layover detail per stop.
- `price`: an integer.

## Terms-of-service posture

SerpApi is the same unsanctioned Google-Flights-scrape class as fli — a
robustness upgrade over one library's parser, not a compliance change.
SerpApi's own docs say the engine "allows you to scrape flight results
from Google Flights", and the pricing page markets a "U.S. Legal Shield".
Assessed 2026-07-13.

## Sources consulted

- https://serpapi.com/google-flights-api
- https://serpapi.com/pricing
- getaway research sweep, 2026-07-13
