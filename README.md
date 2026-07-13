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

The skill reads your travel profile — home airport, program balances,
travel documents — pins the brief into per-trip memory, sweeps whole
regions, and pitches concrete options, layovers judged alongside the
miles. One
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

### Get creative with routings

A direct award to a second-tier city rarely exists, and when it does it
costs double. Lie-flat space to the big gateways is everywhere. Split
the trip instead:

```text
Fly me lie-flat to Asia in November — happy to land at a hub like NRT
and hop the last leg on a cash ticket.
```

The skill composes hybrid routings beside direct awards and compares
them on total cost. A cash hop under four hours books economy; longer
books business:

```text
SFO  NRT  aeroplan business  88000 miles + $118 taxes  lie-flat
NRT  TPE  cash economy       $96                       3h45m hop
```

## Preferences and trip memory

Two stores back planning. Your global profile lives at
`~/.getaway/preferences.json` — the always-true facts: home airport, origin
airports, airlines to avoid, airports you never connect through, elite
statuses, per-program points balances, layover style and the cities worth
a long stop, travel documents — passports, residency, standing visas —
and the `op_ref` pointer for the API key. Each trip gets its own memory at
`~/.getaway/plans/<slug>.json`, filled
in as planning pins down the dates, cabin, party, regions, and destinations
to skip. A destination skipped there is ruled out only as the trip's final
stop; it stays valid as a connection or positioning stop.
First use offers `/getaway:onboard`, an interactive onboarding form
(cc-present) that collects the profile up front: home airport, avoid
lists, points balances, travel documents, and the 1Password key
reference. Skip the form
to accept the defaults.
The form arrives pre-filled: [gogcli](https://gogcli.sh) scans Gmail
read-only for airline and bank statement emails, and the
agent-browser-with-cookies skill reads live balances and elite tiers from
the airline and bank sites you're already logged into, behind one Touch
ID tap. Nothing is saved until you confirm
the form.
Balances drift, so `/getaway:refresh` re-reads them on demand — airline
programs and bank transferable points both — from the sites you're
logged into, and merges the deltas straight back into the profile.
Both schemas live in [skills/getaway/SKILL.md](skills/getaway/SKILL.md).

## Reference

The full API surface behind the skill — endpoints, params, data shapes, quota,
and per-program coverage — lives in
[docs/seats-aero-api.md](docs/seats-aero-api.md). A seats.aero Pro
subscription is required for the API key; Pro keys get 1,000 calls per day.

Licensed under [PolyForm-Noncommercial-1.0.0](LICENSE).
