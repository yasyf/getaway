# Doctrine

The settled rulings behind the pipeline. Each one binds the CLI, the workflow, and the skill prose alike; the cc-notes id cites the record that settled it. Changing a ruling means superseding its record — never drifting from it in one surface.

## Preferences never gate

Physical feasibility, confirmed constraints, and vetoes may gate; preferences never do. The complete hard set: structural journey validity, known seat insufficiency on the live-expanded row (`unknown` stays visible with a verification warning), endpoint and transit vetoes, hard-avoid carriers on any expanded segment, and explicitly confirmed `constraints`. Everything else — dates, trip length, cabin, mileage target, weekdays, layover style, balances, elite status, seat verdicts — orders or annotates through fit facts and tier verdicts. A zero balance never drops a program from a sweep; a journey past the preferred window composes and carries its named miss. (cc-notes fdd212c0)

## The window is the preferred envelope; fit never gates

Soft dates sweep the stated range plus seven days of padding, independent of preference strength; confirmed dates sweep exact bounds. All cabins ride one call with `include_filtered=true`, so near-misses exist in the cache before judgment. Fit facts and `preference_misses` are CLI-computed, the renderer always shows the misses, and assess surfaces up to two notable stretches from beyond the presentation cut. (cc-notes fdd212c0)

## The journey is the unit

One normalized Journey — direct, hybrid, round-trip, open-jaw — with one concrete return leg on round trips. Returns sweep in-run from the prior leg's reached dests; pairing happens before ranking; hybrids compose at expand with legs typed `award|cash`, and assess judges whole journeys, cash hops included. Unpaired outbounds are a visible lead class ordered by outbound mileage with cache age, and `partial`/`not_run`/`failed` search states never read as "no space". Composition, fit facts, cost vectors, and misses are deterministic CLI code, never agent prompts. (cc-notes 9d2d74b3)

## Ranking runs in lanes, never a score

Cost lane first: same-program journeys band on scalar bookable mileage; mixed-program and cash-bearing journeys rank as per-program vectors on a Pareto front, cash cents its own axis. Judgment lane within a cost tier: tier verdicts consumed lexicographically — `primary` orders, `secondary` breaks ties, `note` annotates and never reorders. No composite score exists anywhere; unknown is neutral. The presentation cut of six applies only after assess. (cc-notes 3f445d24)

## `barely` demotes in-band

A `barely` seat-quality verdict soft-demotes within its cost tier: the journey sinks below every true lie-flat, keeps its spot, and carries an explicit warning at presentation. An unknown or unclassified product ranks neutral — never demote what the table doesn't condemn. (cc-notes fdd212c0)

## Enhancers annotate and demote, never gate

Background verification is incremental enhancement: fire-and-forget agents that never block the walk, the board, or the user, whose failure is silence, and whose results land only through the flock-guarded `enhance merge` — never a whole-file artifact write, never a journey mutation. Rank folds them as facts plus deterministic verdicts: `gone` and `degraded` demote within the cost tier, `confirmed` annotates and never promotes — ordering must not depend on verifier reach. A verified-gone finalist keeps its board row with a prominent flag. (cc-notes 81efec6c)

## One artifact per trip; rounds are phases

A trip is one live cc-present artifact, and its phases — intake, finalists, head to head, booking sheet — are rounds on that one board, never separate pushes or separate artifacts. Each phase transition is a `submit`, a drained `outcomes`, and one full `push` filling the round the submit opened; inside a round only `update-block`, `reply`, and `remove-block` touch the board, because a full push mid-round stamps the whole document into the current round and drops the live blocks a verifier is still annotating. A row the human has seen never disappears: a verified-gone finalist keeps its row with a prominent flag, and phase-namespaced ids (`in-`, `fin-`, `cmp-`, `book-`) keep a reused id from inheriting a stale verdict across rounds. (cc-notes 894403d6)

## Expansion is budgeted, and truncation is disclosed

The per-endpoint expansion budget — default twelve, overridable per trip through `plan.tuning.expansion_budget_per_endpoint` — diversifies selection across date, program, and cost cohorts, truncation always disclosed. No global cap: one hot endpoint never starves the others into false returnlessness. (cc-notes fdd212c0, 664182ee)

## QAF first for Africa; pseudo-codes never reach filters

Africa sweeps lead with the `QAF` pseudo-code on `/search`, even though it appears in no UI-documented list; the per-program continent fallback sweeps all programs funded-first, never trimmed. A pseudo-code is a sweep operand the server expands — every feasibility check compares the expanded concrete airports recorded on the leg. (cc-notes fdd212c0)

## Vetoes bind endpoints only

The avoid union — prefs `avoid_destinations` plus the trip's `avoid_final_destinations` — vetoes where a trip may end, never where it passes through. A gateway is a waypoint: NRT on the avoid list stays fully valid as the hub an award lands at. The CLI enforces the flip side at `trip set`, rejecting a leg whose `dests` intersect the union unless it targets `$origins`. (cc-notes 9d2d74b3)

## Cabin is decided per leg, by judgment

The cash-or-award cabin call happens leg by leg — a judgment fed by duration fit facts, not a cutoff constant — never once per trip. A business award with an economy cash hop onward is the intended shape, not an inconsistency. (cc-notes 9d2d74b3)

## Composition is bounded, and every cut is disclosed

Two-leg plans compose exhaustively. Longer chains cheap-rank on (miles, cash) under the plan's beam width before any expansion spends quota, the cut disclosed as `beam_cut`; `optional` legs fan into include/skip variants on the same front, sweep coverage provably enveloping every skip variant, and a variant opening on the homeward leg is structurally excluded. A plan whose legs declare no cash or `either` hop compiles zero hybrid nodes — absence is structural, not a reduced mode. (cc-notes 664182ee, 75cfaf49)

## Declared inputs re-validate at read; scout adds, never gates

`legs/manual.json` and `legs/<id>/scout.json` are agent-authored inputs, not derived artifacts: they never fingerprint-invalidate, so every read re-validates against the current plan. A stale or spec-violating artifact is a typed, remedied rejection — never a crash, a silent truncation, or a NoData masquerading as an empty market. A manual chain covers every mandatory leg in plan order and may skip optional legs; its subset is its variant, priced through the same deterministic lanes as composed journeys. Scout airports feed a discover leg's sweep beside any declared buckets or program sweeps — discovery widens retrieval, never narrows it. (cc-notes 664182ee)

## Lodging derives from observed clocks

Stay intervals come from actual journey timestamps — a cash hop's arrival from the priced quote's real arrival datetime. An unknown arrival defers lodging; nothing ever guesses a night. Lodging scope is explicit in the plan, and stays spend zero seats.aero quota. (cc-notes 9d2d74b3)

## No schema versioning

No version fields, no migrations, ever — JSON documents validate by shape alone. A pre-v2 preferences file is rejected loudly and regenerated through onboarding. SQLite's `user_version` is a cache-invalidation stamp on a derived artifact: on mismatch the database is deleted and recreated, and nothing durable depends on it surviving. (cc-notes 1920b34, 7041bf5)

## One writer per session, flock across sessions

Within a session, every durable write — `prefs set` and its siblings, `trip set`, `trip log` — runs at the main level; workflow agents write only their own artifact files, one writer per file by construction. Concurrent top-level sessions are arbitrated by the CLI's `atomic_update` flock, not by agent discipline.

## Quota is reserved at the boundary

Every `SeatsClient._get()` call reserves one unit under the store flock before the request, releases the lock for the network, and reconciles the response header monotonically after — parallel processes cannot jointly cross the `--quota-floor`, and an out-of-order response never restores quota. Freshness self-skips check before reserving; a skipped node spends nothing. (cc-notes 9d2d74b3)

## Factors stay judgment-shaped

No composite numeric score exists anywhere. Factors carry tiers — `primary` orders within a cost tier, `secondary` breaks ties, `note` annotates — and every journey carries a per-factor verdict with an evidence line that travels to presentation intact. Fit facts are CLI-computed primitives the factors judge; the factor registry is closed data, a row in `factors.json` plus a collector spec. (cc-notes a46065d2)

## The contract is the CLI; the script is a template

`trip compile`/`trip explain` emit the graph: node commands, dependencies, freshness, quota costs, and model routing. Walkers splice emitted commands and trust node checkpoint state, never agent prose; `plan-trip.js` is the codified reference walker, not a mandate. Routing rides the graph — mechanical runners on cheap models, judgment on research models, and fable never runs a trip-planning subagent. (cc-notes 1f828267)
