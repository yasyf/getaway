---
name: getaway
description: Plans award flights using the seats.aero Partner API. Triggers when the user wants to plan an award flight or trip on points or miles, find award availability or saver space between airports, compare mileage programs for a route, or mentions seats.aero. Requires a seats.aero Pro API key in SEATS_AERO_API_KEY.
allowed-tools: Bash(curl:*)
---

# getaway

Plan award flights with cached availability from the
[seats.aero Partner API](https://developers.seats.aero/). The full API
surface, data shapes, and program coverage live in
[docs/seats-aero-api.md](../../docs/seats-aero-api.md).

## Auth

Every request needs a seats.aero Pro API key in the `Partner-Authorization`
header. Keys start with `pro_` and are generated on the seats.aero Settings
page, under the API tab. Read the key from `SEATS_AERO_API_KEY`, either
exported in the environment or set in the repo's gitignored `.env`. If the
key is missing or empty, stop and ask the user to set it; nothing works
without it.

The base URL is `https://seats.aero/partnerapi`. Pro keys get 1,000 calls per
day, resetting at midnight UTC. Check the `X-RateLimit-Remaining` response
header and tell the user when it runs low.

## Smoke call

One cached search for business-class award space, SFO to Tokyo, over the
first two weeks of September:

```bash
curl -fsS "https://seats.aero/partnerapi/search?origin_airport=SFO&destination_airport=NRT,HND&start_date=2026-09-01&end_date=2026-09-14&cabins=business&take=25" \
  -H "Partner-Authorization: $SEATS_AERO_API_KEY" \
  -H "Accept: application/json"
```

Results come back as a `data` array of availability objects, one per
route+date+program. Cabin-specific fields are keyed Y/W/J/F, so business
class reads as `JAvailable`, `JMileageCost`, and `JRemainingSeats`. Cached
snapshots can be hours to days old; always surface each result's `UpdatedAt`
timestamp alongside it.

## Status

This skill is a skeleton. Auth and the smoke call above are real, but the
planning workflow is not written yet: fanning out across programs, budgeting
the daily quota, and pulling trip-level detail with booking links all remain
to be built. Until then, treat docs/seats-aero-api.md as the source of truth
and compose calls by hand.
