# Bank transfer partners

The static bank-to-program transfer map behind the
[affordability math](SKILL.md#affordability-and-top-ups): which
seats.aero programs each bank's points reach, and at what ratio.
`british` is a live beta source (rows appear in `/search`) not yet in
the API's documented list of 26. Bank slugs are the
`balances.transferable` keys. Verified 2026-07-12 against the banks'
official partner pages; the partner core moves rarely, while current
rates and promos get a `WebSearch` at quote time.

## Transfer matrix

Ratios read bank:program — 1:0.8 turns 1,000 bank points into 800
program points. `—` marks no path.

| Program | `amex` | `chase` | `citi` | `capitalone` |
|---|---|---|---|---|
| `aeromexico` | 1:1.6 | — | — | 1:1 |
| `aeroplan` | 1:1 | 1:1 | — | 1:1 |
| `american` | — | — | 1:1 | — |
| `british` (beta) | 1:1 | 1:1 | — | 1:1 |
| `delta` | 1:1 | — | — | — |
| `emirates` | 1:0.8 | — | 1:0.8 | 1:0.75 |
| `etihad` | — | — | 1:1 | 1:1 |
| `finnair` | — | — | — | 1:1 |
| `flyingblue` | 1:1 | 1:1 | 1:1 | 1:1 |
| `jetblue` | 1:0.8 | 1:1 | 1:1 | 1:0.6 |
| `qantas` | 1:1 | — | 1:1 | 1:1 |
| `qatar` | 1:1 | — | 1:1 | 1:1 |
| `singapore` | 1:1 | 1:1 | 1:1 | 1:1 |
| `turkish` | — | — | 1:1 | 1:1 |
| `united` | — | 1:1 | — | — |
| `virginatlantic` | 1:1 | 1:1 | 1:1 | 1:1 |

No bank reaches the other 11 programs: `alaska`, `lufthansa`,
`velocity`, `eurobonus`, `connectmiles`, `azul`, `smiles`, `ethiopian`,
`saudia`, `frontier`, and `spirit`. A shortfall there has no transfer
fix; the options are buying points or a different program.

## Quirks that change the math

- `british`, `qatar`, and `finnair` share the Avios currency and pool
  it freely on avios.com — a transfer landing in any of the three funds
  awards in all of them. That gives Citi an indirect `british` path
  (via `qatar`) and every Avios-reaching bank an indirect `finnair`
  path beyond Capital One's direct one.
- Amex adds a federal excise-tax offset fee on transfers to US carriers
  (`delta`, `jetblue`): about $0.60 per 1,000 points, capped at $99.
  `virginatlantic` books Delta-operated flights without it.
- Citi's ratios require a Strata Premier, Strata Elite, or legacy
  Prestige card; other ThankYou-earning cards may have no transfer
  access at all.
- Capital One's `virginatlantic` partner is nominally Virgin Red;
  linked accounts share one Virgin Points balance that books Flying
  Club awards directly.
- Transfers move in fixed blocks — Chase in 1,000-point increments, and
  only from an account holding a transfer-eligible card — so round a
  shortfall up to the next block before calling it covered.
- Most pairs land instantly. The slow ones: `delta` from Amex up to
  48h, `singapore` 24–48h from Amex or Chase, `aeromexico` from Amex
  about a day — flag the lag when seats are scarce.
- Recent cuts: Amex ended `etihad` transfers 2026-06-30; Chase ended
  `emirates` 2025-10-16; Amex devalued `emirates` from 1:1 to 1:0.8 on
  2025-09-16 and Citi on 2025-07-27 (Capital One's 1:0.75 is unrelated
  and unchanged).

Sources: the official
[Amex](https://global.americanexpress.com/rewards/transfer),
[Chase](https://www.chase.com/personal/credit-cards/education/basics/how-to-transfer-chase-ultimate-rewards-points),
[Citi](https://www.thankyou.com/partnerProgramsListing.htm), and
[Capital One](https://www.capitalone.com/learn-grow/money-management/venture-miles-transfer-partnerships/)
partner pages.
