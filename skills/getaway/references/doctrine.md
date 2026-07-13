# Doctrine

The settled rulings behind the pipeline. Each one binds the CLI, the workflow, and the skill prose alike; the cc-notes id cites the record that settled it. Changing a ruling means superseding its record — never drifting from it in one surface.

## Biases never gate

The only filters anywhere in the pipeline are hard-avoid airlines and the endpoint-veto union. Everything else — soft-avoid airlines, departure-day preferences, balances, elite status, seat verdicts, funding position — orders or annotates, never removes. A zero balance never drops a program from a sweep; a soft-avoided airline sinks in the sort and keeps its row. The shortlist's SQL WHERE clause carries exactly the two filters and nothing more. (cc-notes 98be9f3)

## `barely` demotes in-band

A `barely` seat-quality verdict soft-demotes within the mileage band: the finalist sinks below every true lie-flat, keeps its spot, and carries an explicit warning at presentation. An unknown or unclassified product ranks neutral — never demote what the table doesn't condemn. (cc-notes 98be9f3)

## Classification runs behind the buffer

Expand and classify the full expansion buffer — twice `max_finalists`, capped at 12 — and truncate at rank, never before. Truncating first is how a yin-yang seat outranks a true flat bed: the re-rank needs real products and real connections to sort. The low-quota path drops the buffer first, knowingly. (cc-notes 98be9f3)

## QAF first for Africa

Africa sweeps lead with the `QAF` pseudo-code on `/search`, even though it appears in no UI-documented list; its observed expansion is a floor. The per-program continent fallback sweeps all programs ordered funded-first, never trimmed to the funded ones. (cc-notes 98be9f3)

## Vetoes bind endpoints only

The avoid union — prefs `avoid_destinations` plus the trip's `avoid_final_destinations` — vetoes where a trip may end, never where it passes through. A gateway is a waypoint: NRT on the avoid list stays fully valid as the hub an award lands at. The CLI enforces the flip side at `trip set`, rejecting a `hybrid.onward_dests` set that intersects the union. (cc-notes 03b201f)

## Cabin is decided per leg

The cash-or-award cabin call happens leg by leg, never once per trip. A business award with an economy cash hop onward is the intended shape, not an inconsistency. (cc-notes 03b201f)

## The 240-minute cutoff is a code constant

Cash and stitched onward legs book economy at or under 240 minutes and business above. The cutoff lives in `constants.py`, not in `trip.json`. A per-trip override is a logged decision that adjusts the plan, not a knob on the constant. (cc-notes 03b201f)

## Cost comparison happens at presentation

Composition never prunes by cost. Hybrids, stitches, and direct awards all reach the board, where they compete on total cost — miles plus taxes plus cash — with the evidence visible. Pruning earlier hides the comparison the user is owed. (cc-notes 03b201f)

## Hybrid fan-out is bounded

Hybrid composition works only the explicit `gateways` and `onward_dests` sets in the plan, capped by `max_hybrids`. No phase invents gateways or onward destinations beyond what the plan declares. (cc-notes 03b201f)

## Returns are a second invocation

A round trip is two workflow runs: the outbound, then a return invocation with `return-` prefixed artifacts once the outbound settles. The outbound's `return_viability` factor reads only the cache, spends zero quota, and flags unverified returns — it never filters an outbound finalist. (cc-notes 03b201f)

## Hybrid-absent is inert

A plan without a `hybrid` key runs zero hybrid phases and follows a path identical to a non-hybrid plan. Absence means off — not defaults, not a reduced mode. (cc-notes 03b201f)

## Ranking currency is bookable mileage

Rank orders on the integer mileage cost the user actually books — the normalized cost `expand` returns from `/trips/{id}` — never on `/search`'s string-typed teaser, and never on a synthetic value score. (cc-notes 876a964)

## No schema versioning

No version fields, no migrations, ever — JSON documents validate by shape alone. SQLite's `user_version` is a cache-invalidation stamp on a derived artifact: on mismatch the database is deleted and recreated, and nothing durable depends on it surviving. (cc-notes 1920b34, 7041bf5)

## One writer per session, flock across sessions

Within a session, every durable write — `prefs set` and its siblings, `trip set`, `trip log` — runs at the main level; workflow agents write only their own artifact files, one writer per file by construction. Concurrent top-level sessions are arbitrated by the CLI's `atomic_update` flock, not by agent discipline.

## Quota is budgeted, recorded, and gated

The budget is 1,000 calls per day. Every API call records its quota headers as events; `quota` reports from those records without spending a call, and `quota check --floor N` gates API-spending phases. Under parallel fan-out, the gate trusts each agent's reported `quota_remaining`, not the on-disk history.

## Factors stay judgment-shaped

No composite numeric score exists anywhere. Factors carry tiers — `primary` orders within a mileage band, `secondary` breaks ties, `note` annotates — and every finalist carries a per-factor verdict with an evidence line that travels to presentation intact. The factor registry is closed data, a row in `factors.json` plus a collector spec; a factor that wants its own class hierarchy is cruft. (cc-notes a46065d2)
