# ![getaway](docs/assets/readme-banner.webp)

**Plan award trips from Claude Code.** getaway sweeps seats.aero across 28 mileage programs, composes whole journeys — flights out and home, hotel award nights via rooms.aero — and judges every option like you would: seat quality, layovers, status earning, cash-fare anomalies.

[![CI](https://img.shields.io/github/actions/workflow/status/yasyf/getaway/ci.yml?branch=main&label=ci)](https://github.com/yasyf/getaway/actions/workflows/ci.yml)
[![License: PolyForm-Noncommercial-1.0.0](https://img.shields.io/badge/License-PolyForm--Noncommercial--1.0.0-blue.svg)](https://github.com/yasyf/getaway/blob/main/LICENSE)

## Get started

```text
/plugin marketplace add yasyf/captain-hook
/plugin marketplace add yasyf/getaway
/plugin install getaway@getaway
```

Add the `yasyf/captain-hook` marketplace first: getaway's hooks ride the
`captain-hook` plugin, and Claude Code auto-installs that dependency only
when its marketplace is already added. Upgrading an existing install needs
the same order — marketplace, then `claude plugin install` again, since
`claude plugin update` resolves only plugins it already knows.

You need [uv](https://docs.astral.sh/uv/) on PATH — the planning engine is
a bundled Python CLI — and a seats.aero Pro API key, generated on the
seats.aero Settings page under the API tab:

```bash
export SEATS_AERO_API_KEY=pro_YOUR_KEY
```

Run `/getaway:onboard` once. The form arrives pre-filled from Gmail and
the airline and bank sites you're already logged into, and nothing is
written until you hit Submit. Skipping it is fine: planning runs on a
neutral profile, and balances bias ranking, never gate it. Then hand
over the trip in one sentence, as messy as you like:

```text
I want to go away for roughly a week, leaving in the next couple days, and want something outside north america, warm, beachy, and has cheap points tickets for business class. want to avoid the common places we always go like seoul or tokyo
```

Claude pins the ask into a trip, derives a judgment profile from every
clause — business class puts seat quality and affordability first,
"warm, beachy" activates destination context, "always go" writes Seoul
and Tokyo to your durable avoid list — and dispatches the planning
pipeline. Finalists land on a live board, one evidence line per active
factor:

<img src="docs/assets/board.webp" alt="getaway finalist board from the ask above: an option picker over ranked journeys, itinerary cards with taxes, remaining seats, booking links, and freshness stamps, and per-factor evidence lines" width="760">

Driving with an agent? Paste this:

```text
/plugin marketplace add yasyf/captain-hook
/plugin marketplace add yasyf/getaway
/plugin install getaway@getaway
```

---

## Use cases

### Find saver space for a specific route and dates

Checking United, Aeroplan, and Alaska one site at a time, for the same
seat, is an evening gone. Ask once instead:

```text
Any business award space from SFO to NRT or HND, September 1–14?
```

One sweep covers all 28 programs, and every row carries per-cabin
mileage, remaining seats, and a freshness stamp. Rows land in a local
cache, so follow-ups — other dates, other cabins — answer without
spending another API call.

### Spend a stranded points balance

A six-figure Aeroplan balance with no destination in mind is money
rotting. Flip the search around:

```text
Where can Aeroplan take me in business from North America in October?
```

Bulk availability scans one program across whole regions, so the answer
is a list of real routes with real award space — island space most
people never think to search included:

```text
2026-07-11  CMB  MLE  aeroplan  12500  9  FZ, GF
2026-07-11  TNR  MRU  aeroplan  12500  9  MK
2026-07-11  BLR  MLE  aeroplan  22500  1  AI, GF
2026-07-11  FRA  FUE  aeroplan  22500  1  2L, LX, WK
```

### Get creative with routings

A direct award to a second-tier city rarely exists, and when it does it
costs double. Lie-flat space to the big gateways is everywhere. Split
the trip instead:

```text
Fly me lie-flat to Asia in November — happy to land at a hub like NRT
and hop the last leg on a cash ticket.
```

The planner composes hybrid routings — gateway awards with cash hops,
open jaws, two-award stitches — beside direct awards and compares them
on total cost. A cash hop at or under four hours books economy; longer
books business:

```text
SFO  NRT  aeroplan business  88000 miles + $118 taxes  lie-flat
NRT  TPE  cash economy       $96                       3h45m hop
```

### Pick up a trip where you left off

A trip plan that dies with the session is a trip planned twice. Every
constraint, sweep, and finalist persists per trip under
`~/.getaway/trips/<slug>/`, so resuming is one ask:

```text
Pick up the warm-beachy trip where we left off.
```

The resume brief carries the pinned constraints, per-phase freshness,
finalists so far, and expiring credits. Phases whose inputs haven't
changed skip wholesale — a resumed plan spends zero API quota until you
change something.

## How it plans

Every real ask becomes a trip: the verbatim ask, pinned constraints and
preference lanes, and a judgment profile derived from both. The bundled
CLI compiles each trip into an execution graph — sweeps, shortlists,
journey composition, evidence, assess, rank — and the shipped workflow
walks it. Preferences order and annotate but never gate, mileage stays
dominant with judgment reordering only inside a band, and a trip with
lodging in scope picks up hotel award nights from rooms.aero along the
way. The full doctrine lives in
[skills/getaway/references/](skills/getaway/references/).

## The skills

The plugin ships three skills, each triggered by its own asks:

| Skill | When | What happens |
|---|---|---|
| `getaway` | Any trip, route, or award ask | Pins the trip, runs the pipeline, presents finalists with evidence |
| `getaway:onboard` | First run, or "set up my travel preferences" | A pre-filled preferences form: [gogcli](https://gogcli.sh) scans Gmail read-only, and live balances and statuses read from logged-in airline and bank sites behind one Touch ID tap; nothing is saved until Submit |
| `getaway:refresh` | "Refresh my balances", "what credits are expiring" | Re-reads balances, elite statuses, and trip credits from the logged-in sites — Gmail statement fallback for banks — and merges the deltas into your profile |

## Requirements

- A seats.aero Pro subscription for the API key. Pro keys get 1,000
  calls per day; the planner tracks quota and answers follow-ups from
  cache.
- [uv](https://docs.astral.sh/uv/) — the planning engine is a bundled
  Python CLI run through `uv run`; the first call builds its
  environment.
- The 1Password `op` CLI, when the key comes from an `op_ref` in
  `~/.getaway/preferences.json` instead of the environment — a secret
  reference like `op://Vault/item/field` that the CLI resolves with
  `op read` whenever the env var is unset.
- The cc-present plugin, for the interactive boards — getaway ships its
  block pack at `.claude/components`.

## Reference

The raw Partner API surface — endpoints, params, data shapes, quota, and
per-program coverage — lives in
[docs/seats-aero-api.md](docs/seats-aero-api.md). The CLI documents
itself: `uv run --project <plugin-root>/cli getaway --help`.

Licensed under [PolyForm-Noncommercial-1.0.0](LICENSE).
