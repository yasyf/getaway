# ![getaway](docs/assets/readme-banner.webp)

**Plan award flights without leaving Claude Code.** getaway is a Claude Code
plugin that searches seats.aero award availability across 26 mileage programs.

[![CI](https://img.shields.io/github/actions/workflow/status/yasyf/getaway/ci.yml?branch=main&label=ci)](https://github.com/yasyf/getaway/actions/workflows/ci.yml)
[![License: PolyForm-Noncommercial-1.0.0](https://img.shields.io/badge/License-PolyForm--Noncommercial--1.0.0-blue.svg)](https://github.com/yasyf/getaway/blob/main/LICENSE)

## Get started

```text
/plugin marketplace add yasyf/getaway
/plugin install getaway@getaway
```

getaway needs a seats.aero Pro API key, generated on the seats.aero Settings
page under the API tab:

```bash
export SEATS_AERO_API_KEY=pro_YOUR_KEY
```

Then ask for a trip:

```text
Find business-class award space from SFO to Tokyo in the first two weeks of September.
```

Claude picks up the getaway skill, queries seats.aero's cached search across
every supported program in one call, and reports what's bookable: mileage cost
and seats remaining per cabin, plus how fresh each snapshot is.

Driving with an agent? Paste this:

```text
/plugin marketplace add yasyf/getaway
/plugin install getaway@getaway
```

---

## Use cases

### Find saver space for a specific route and dates

Checking United, Aeroplan, and Alaska one site at a time, for the same seat,
is an evening gone. Ask once instead:

```text
Any business award space from SFO to NRT or HND, September 1–14?
```

One cached-search call covers all 26 programs. Results carry per-cabin mileage
cost, remaining seats, and an `UpdatedAt` stamp so you know how stale each
snapshot is.

### Pay the fewest miles for the same seat

The same cabin on the same route prices wildly differently across programs.
Sort by cost instead of guessing:

```text
Which program books LAX to London in business for the fewest miles this fall?
```

The skill orders cached results by `lowest_mileage`, so the cheapest
redemption surfaces first.

### Spend a stranded points balance

A six-figure Aeroplan balance with no destination in mind is money rotting.
Flip the search around:

```text
Where can Aeroplan take me in business from North America in October?
```

Bulk availability scans one program across whole regions, so the answer is a
list of real routes with real award space.

## Reference

The full API surface behind the skill — endpoints, params, data shapes, quota,
and per-program coverage — lives in
[docs/seats-aero-api.md](docs/seats-aero-api.md). A seats.aero Pro
subscription is required for the API key; Pro keys get 1,000 calls per day.

Status: skeleton — auth and cached search are wired; the planning workflow
(program fan-out, quota budgeting, booking links) is still being built.

Licensed under [PolyForm-Noncommercial-1.0.0](LICENSE).
