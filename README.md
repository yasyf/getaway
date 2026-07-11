# ![getaway](docs/assets/readme-banner.webp)

**Plan award flights from Claude Code.** getaway searches seats.aero award space across 26 mileage programs and plans the trip around your preferences — dates, cabins, balances, booking links.

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

Prefer 1Password? Set `op_ref` in `~/.getaway/preferences.json` to the key's
secret reference (`op://Vault/item/field`); the skill reads it with `op read`
whenever the env var is unset.

Then ask for a trip:

```text
Find business award space from SFO to Lisbon, Barcelona, or Athens in September.
```

Claude picks up the getaway skill, sweeps seats.aero's cached search across
every supported program in one call, and reports what's bookable — date,
route, program, miles, business seats, operating airlines:

```text
2026-09-14  SFO  ATH  flyingblue  177000  6  AF
2026-09-29  SFO  ATH  flyingblue  177000  5  AF, KL
2026-10-05  SFO  ATH  flyingblue  177000  9  AF, KL
2026-10-04  SFO  ATH  american     57500  0  BA
2026-10-05  SFO  ATH  american     57500  0  BA
2026-10-06  SFO  ATH  american     57500  0  BA, IB
2026-10-07  SFO  BCN  american     57500  0  BA
2026-09-12  SFO  BCN  american    132500  0  AA
```

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

### Go somewhere warm for a week, skipping the usual suspects

Warm, a week, on points: that's the whole brief, and the obvious hubs are
exactly where you don't want to land. Hand it over as-is:

```text
I want to go somewhere warm for a week on points, skipping the usual suspects.
```

The skill reads your preferences — home airport, avoided destinations,
program balances — sweeps whole regions, and pitches concrete options. One
Aeroplan bulk scan surfaces island space most people never think to search:

```text
2026-07-11  CMB  MLE  aeroplan  12500  9  FZ, GF
2026-07-11  TNR  MRU  aeroplan  12500  9  MK
2026-07-11  BRU  ACE  aeroplan  22500  8  2L, AZ, BT, LX, SN, WK
2026-07-11  ZRH  FUE  aeroplan  22500  9  WK
```

<details>
<summary>More of the same scan: Maldives, Mauritius, and Canary Islands business space under 25k points</summary>

```text
2026-07-11  CMB  MLE  aeroplan  12500  9  FZ, GF
2026-07-11  TNR  MRU  aeroplan  12500  9  MK
2026-07-12  CMB  MLE  aeroplan  12500  9  FZ, GF
2026-07-12  TNR  MRU  aeroplan  12500  9  MK
2026-07-13  CMB  MLE  aeroplan  12500  9  FZ, GF
2026-07-13  TNR  MRU  aeroplan  12500  9  MK
2026-07-14  CMB  MLE  aeroplan  12500  9  FZ, GF
2026-07-11  BLR  MLE  aeroplan  22500  1  AI, GF
2026-07-11  BOM  MLE  aeroplan  22500  1  AI, EK, GF
2026-07-11  BRU  ACE  aeroplan  22500  8  2L, AZ, BT, LX, SN, WK
2026-07-11  DEL  MLE  aeroplan  22500  6  AI, FZ, GF
2026-07-11  FRA  FUE  aeroplan  22500  1  2L, LX, WK
2026-07-11  MUC  FUE  aeroplan  22500  1  2L, LX, WK
2026-07-11  STR  ACE  aeroplan  22500  8  BT, WK
2026-07-11  STR  FUE  aeroplan  22500  1  2L, BT, WK
2026-07-11  ZRH  FUE  aeroplan  22500  9  WK
```

</details>

## Preferences

The skill keeps your travel profile at `~/.getaway/preferences.json`. It
creates the file on first use and folds in what it learns as you plan: your
home airport, cabin preference, destinations you never want, airlines to
avoid, per-program points balances, and the `op_ref` pointer for the API key.
First use also opens an interactive onboarding form (cc-present) that
collects the profile up front: home airport, cabin, avoid lists, points
balances, and the 1Password key reference. Skip the form to accept the
defaults.
The full schema lives in [skills/getaway/SKILL.md](skills/getaway/SKILL.md).

## Reference

The full API surface behind the skill — endpoints, params, data shapes, quota,
and per-program coverage — lives in
[docs/seats-aero-api.md](docs/seats-aero-api.md). A seats.aero Pro
subscription is required for the API key; Pro keys get 1,000 calls per day.

Licensed under [PolyForm-Noncommercial-1.0.0](LICENSE).
