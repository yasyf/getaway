# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The journey engine: one dispatch plans the whole trip — outbound,
return, hybrids, and hotel stays — as ranked journeys instead of
annotated outbounds. This is a clean cutover: a pre-v2
`preferences.json` is rejected loudly and regenerates through
onboarding, and in-flight v1 trips are discarded, not migrated.

### Changed
- The skill flips from a twelve-step mandate to a primitives catalog:
  "Invent the shape, then price it" leads SKILL.md with the leg-intent
  vocabulary — chained and open-jaw endpoints, `either`-mode legs, stay
  stops, optional variants, discover scouts, per-trip tuning, manual
  chains — and the canonical warm-beachy walk demotes to one worked
  example in conventional ids. The reference docs and doctrine follow:
  planning.md sheds the last removed-key mechanics, "Hybrid fan-out is
  bounded" generalizes to composition bounds, and two new rulings land
  (declared inputs re-validate at read; scout adds, never gates).
- The leg-intent cutover: `plan.legs` — an ordered list of intents
  (origins, dests, `award|cash|either` mode, per-leg windows,
  `stay_nights` stops, `"$origins"` as the homeward marker, open jaws
  via explicit later-leg origins) — replaces the closed
  `trip_type`/`hybrid`/`return` schema outright. A journey is any chain
  of concrete legs satisfying the intents: multi-city, positioning cash
  legs, and N-leg stitches compose, price, and rank in the same
  deterministic engine as the canonical round trip, whose compiled
  graph and composed journeys stay byte-identical to the old engine
  (pinned). Conventional leg ids keep sweep keys, artifact paths, and
  checkpoints stable; two-leg plans compose exhaustively while longer
  chains cheap-rank under a disclosed composition beam; chained legs
  resolve endpoints from their predecessor's reached dests (declared
  dests across cash boundaries), stay-marked boundaries shift retrieval
  dates and bound composition continuity; the walker grammar widened
  regex-pinned. Stored v2 trip docs reject loudly with the re-declare
  remedy — no migrations, per doctrine. Ten adversarial review rounds
  across four stages; every accepted counterexample is a regression
  test (1002 pytest, 50 walker tests at close).
- One-line install. getaway's `captain-hook` and `cc-present` dependencies
  register their own marketplaces on the first session — capt-hook 9.22.0
  self-bootstraps every marketplace a pack declares — so install and upgrade are
  just `/plugin marketplace add yasyf/getaway` then `/plugin install
  getaway@getaway`, no dependency marketplaces to add by hand.

### Added
- The primitives layer on the leg-intent engine — structured mechanisms
  the planning agent calls out to when it deems fit: `plan.tuning`
  per-trip knobs (presentation limit, expansion budget, beam width,
  sweep page budget, date padding) over the former fixed constants;
  `optional: true` legs that fan into with/without variants competing
  on one cheap-rank front, with sweep coverage provably enveloping
  every skip variant (compile of the full plan covers each
  optional-removed variant plan — an equivalence matrix pinned over
  airports, windows, and cash gateway dates); partial-chain leads —
  when no full chain composes on a ≥3-leg plan, the longest bookable
  prefix surfaces with an honest, TTL-aware search state per remaining
  leg; `legs/manual.json` — the agent invents a chain (any
  mandatory-covering, optional-skipping subsequence of the plan), the
  CLI prices it through the same deterministic fit/cost/miss lanes;
  and `dests: {discover: ...}` scout legs — a zero-quota research node
  proposes hub airports that feed the leg's sweep beside any declared
  buckets or program sweeps, adding endpoints, never gating. Mutable
  input artifacts (manual, scout) re-validate against the current plan
  at every read with typed errors — never silent truncation, a stale
  crash, or corruption masquerading as an empty market — and IATA and
  sibling validators reject partial matches, closing a
  trailing-newline hole to the HTTP layer. Thirteen adversarial review
  rounds across five stages; every accepted counterexample is a
  regression test (1113 pytest, 51 walker tests at close).
- Sweep generations: only a complete, filter-honest run supersedes.
  A sweep that fully searched its scope cuts a new generation and
  disclosed rows it proved gone; anything less — a quota stop, a failed
  page, a truncated fetch, a cabin/carrier/direct-filtered lens, a raw
  call missing the widest server view — only adds knowledge to the
  generation pinned at its run start, never erases. The supersede diff
  is bounded by conjunctive scope (endpoints or demonstrated airports
  for region tokens, dates, sources) so nothing outside what a run
  actually searched is ever disclosed or hidden; carried-forward rows
  stay visible undisclosed; incomplete runs cannot backdate a newer
  generation's payload (global watermark at run start); `partial`
  requires data coverage, not just spent calls; raw ingests send
  `include_filtered` like sweeps. Six adversarial review rounds, every
  accepted counterexample pinned as a regression test.
- Shape-agnostic downstream engine, groundwork for arbitrary leg chains
  (bit-identical on today's shapes, pinned by equivalence sweeps): fit
  facts anchor positionally — first leg carries the departure-side
  misses, last leg the return side, gated on the plan-level trip type
  rather than leg-role lookups; stays derive one interval per
  same-airport stop from observed clocks (per-stop lists in
  `stays.json`, deferrals per stop); the ranking cost vector keys cash
  per currency (`$cash:<CCY>`, same-currency components sum, no
  conversion, zero totals carry no axis) with quote currencies
  validated as three uppercase letters at the bridge boundary.
- Retrieval honesty (the missed-booking postmortem fixes): region sweeps
  order by lowest mileage and page through the cursor under
  `SWEEP_PAGE_BUDGET`, so a hot region no longer truncates at one
  arbitrary 1000-row page — a budget-exhausted endpoint with more rows
  reads `partial`/`page_budget`, and a mid-pagination quota stop or HTTP
  failure keeps its fetched rows and true call count (`PaginationError`
  carries partial progress) instead of vanishing into `not_run`.
- Sweep refreshes disclose what they supersede: the store diffs the
  prior latest sweep per label inside the ingest transaction, in-window
  disappearances land in the sweep artifact's provenance
  (`superseded_rows`: exact count, up to 50 ids), and shortlist
  provenance carries the count downstream — supersede semantics stand,
  silence doesn't.
- Home-origin floor in shortlist selection: the cheapest in-window
  home-airport candidate per endpoint is guaranteed a slot ahead of the
  `(date, source)` round-robin, displacement disclosed in the
  truncation record — the exact crowd-out that buried a bookable
  home-origin itinerary.
- Mixed-cabin awards stopped reading as phantoms: `/trips` normalization
  prefers a pure-cabin itinerary but accepts the minimum-mileage
  itinerary with at least one requested-cabin segment, per-segment
  cabins preserved and one shared predicate across the live and cached
  paths — `below_cabin_minutes` fit facts and the cabin gate judge what
  retrieval used to silently discard.
- Confirmed constraints now gate in rank: `constraints.cabin` drops any
  journey whose award segments book below the constrained cabin
  (compared per segment; cash legs exempt — bridge quotes are economy
  by construction) and `constraints.departure_days` drops wrong-weekday
  outbound departures, each disclosed per journey in `dropped`.
  `prefs.avoid_transit` vetoes at composition over real transit points:
  award-leg connections (both sides of every boundary), landside
  self-transfer points, and cash-hop connection airports — origin,
  final destination, and the round-trip turnaround never count.
- Primary preferences reach across cost tiers: `_dominates` carries a
  primary-clears guard — a journey only cost-dominates another when it
  clears every active primary fit preference the other clears, judged
  on CLI-computed `preference_misses` as a set-difference test, never a
  score — and a declared `preferences.<key>.priority` folds into factor
  tiers deterministically (registry default, then declared priority,
  then explicit `judgment.factors` override), so a business-cabin ask
  lifts business journeys with no LLM in the loop.
- Deterministic notable stretches: each primary preference every
  finalist misses surfaces the best-ranked beyond-cut journey that
  clears it — one per preference, deduplicated against assess picks by
  coverage, skipping journeys the verify artifact marks gone or
  degraded.
- Bridge quotes carry `connections` — the intermediate airports of a
  priced cash itinerary, from both the fli and SerpApi backends,
  validated one-per-stop — stored on composed cash legs and exposed in
  fit facts; the transit collector checks each connection airport
  individually.
- `plan.origins` defaults from prefs `origin_airports` (else
  `[home_airport]`) at read time, and `home_airport` joins the prefs
  fingerprint so an origin edit invalidates swept artifacts.
- Walker failure reports carry `stderr_tail` — the failing command's
  last stderr lines, kept even when the retry returns nothing — and an
  exit-3 first attempt backs off 60 seconds before its retry, since
  state-conflict windows are transient.
- Background enhancers — fire-and-forget verification beside a trip in
  planning: `enhance targets <slug> verify` enumerates uncertain rows
  (unknown seat sufficiency, stale cache reads, unverified empty leads)
  and `enhance merge <slug> verify` upserts results concurrency-safe
  into `enhance-verify.json`, which folds at rank/finalize as the
  `availability_verified` factor — `gone`/`degraded` demote in-tier,
  `confirmed` annotates and never promotes, and a verified-gone
  finalist keeps its board row flagged. Auth lanes are public-first
  plus seeded cookie sessions; the CDP lane waits on cookiesync's
  native path. Contract and verifier briefs in
  `skills/getaway/references/enhancers.md`.
- Journeys as the unit of search, ranking, and presentation:
  `plan.trip_type` (`one_way`/`round_trip`) with in-run returns —
  return endpoints resolve mid-run from the outbound shortlist plus
  `onward_dests`, one comma-listed `/search` call sweeps every
  candidate return — and pairing before ranking. `expand run <slug>`
  composes concrete journeys (direct, hybrid, round-trip, open-jaw in
  one representation, hybrid legs typed `award`/`cash`) with
  CLI-computed fit facts, per-program cost vectors, and mandatory
  `preference_misses`; `finalists.json` carries ranked journeys,
  assess-picked notable preference stretches from beyond the cut,
  unpaired outbound leads with cache ages, and honest per-endpoint
  search states (`complete`/`searched_empty`/`partial`/`not_run`/
  `failed`).
- `plan.preferences` and `plan.constraints` branches: preferences are
  `{value, priority: primary|secondary|note}` ordinal lanes that order
  and annotate, never gate; constraints are hard, carry
  `confirmed: true`, and the same key is rejected from both branches.
  Durable prefs and the trip doc are structurally disjoint — each
  write path rejects the other store's keys.
- `trip compile`/`trip explain`: the plan-derived node graph — per-node
  inputs, outputs, freshness TTLs, worst-case quota cost, `requires`,
  ready-made command lines, and `{model, effort}` routing — replacing
  the static phase map. Checkpoints key by node id.
- Ranking in lanes: same-program journeys band on scalar bookable
  mileage; mixed-program and cash-bearing journeys rank as per-program
  vectors on a Pareto front with cash as its own axis; tier verdicts
  consume lexicographically; seat sufficiency is judged on the
  live-expanded `/trips/{id}` row (`sufficient`/`insufficient`/
  `unknown` — only `insufficient` gates).
- Hotels: rooms.aero award stays (`stays intervals`/`stays ingest`),
  scoped by `plan.lodging`, searched after journey composition over
  real timestamps — a cash hop's check-in comes from the priced
  quote's observed arrival, unknown checkouts defer with a reason,
  stays past rooms.aero's five-consecutive-night block cap clamp with
  the clamp disclosed, and per-night points/cash in the property's
  local currency are the source of truth. Zero seats.aero quota. The
  board renders each journey's stays with the new `getaway.stay`
  block (freshness, staleness, deferral reasons included).
- One loyalty registry: `programs.json` rows carry
  `kind: airline|hotel` plus capability fields (`seats_aero`,
  `rooms_aero`, `sells_points`, `gather_auth`); Hyatt, Hilton,
  Marriott, IHG, Choice, and Wyndham join with verified bank transfer
  paths (Chase/Citi/Capital One at 1:1 to Wyndham, Amex 1:2 to
  Hilton, Citi 1:1.5 to Choice, and the rest) and points-pricing
  rows. One `balances.programs` map covers airline and hotel programs
  alike; `registry hosts` emits the browser-read host list with auth
  classes.
- `travel_instruments`, a tagged union replacing the `credits` list:
  `monetary_credit`, `hotel_night_certificate` (program, nights, and
  a points/category/anytime cap), and `companion_fare` — written
  through `prefs instrument-add` (one JSON object on stdin) with
  CLI-generated ids and per-variant validation.
- Quota reservation at the HTTP boundary: every `SeatsClient` call
  reserves a unit under the store flock, releases the lock for the
  network, and reconciles the response header monotonically — parallel
  processes cannot jointly cross the floor. One knob, `--quota-floor N`
  (default 100; `0` spends down deliberately); a floor stop exits 1
  with the work recorded `not_run`, resumable later. Client-boundary
  numeric normalization: `/search`'s string mileage arrives int
  downstream.
- `bridge <slug>`: cash-leg pricing codified over the `flights` (fli)
  library with the hardening the papercuts documented — the OKA/NAH
  Airport-enum alias fix on both encode and decode paths, "today"
  computed in origin-local time, and zero results for a viable route
  surfacing as `failed {retryability}`, never "no cash fare". Each
  quote carries its real departure and arrival clocks.
- `plan-trip.js` rebuilt as the reference graph walker: consumes
  `compile`/`explain`, preflights `requires` (the seeded rooms.aero
  session), splices emitted commands token-guarded, enforces emitted
  routing on every agent (sonnet runners, haiku labels, opus or
  gpt-5.6-terra research via `researchLane: "terra"`; fable is
  rejected), trusts CLI checkpoint state over agent prose (unstamped
  nodes retry once, then fail; null fan-out results take the same
  path), and returns early with options on a shape surprise. Workflow
  args accept object or JSON-string form. `references/workflows.md`
  documents authoring ad-hoc walkers over the same contract.
- Browser-read auth routing in onboarding/refresh: hosts route by the
  registry's `gather_auth` class — cookie hosts keep the seeded
  session fan-out; token/IndexedDB hosts (Delta, AA, United, JetBlue,
  Aeroplan, Qatar, Singapore, Capital One) and Amex's device wall ride
  a live Arc CDP attach — with structural-vs-transient failure
  messaging and a per-host retry ledger so no host silently drops.
- A real finalist-board demo in the README: `docs/assets/board.webp`,
  a cc-present render of `docs/scripts/demo-board.json` — composed from
  a live planning run of the canonical warm-beachy ask on the fixed
  engine (funded profile, live seats.aero data, real assess verdicts),
  carrying rank's own output. `docs/scripts/demo.sh` regenerates the
  capture.
- Held credit cards: a `cards` preference (`{issuer, product}` slugs
  validated against the new `card_products.json` registry and
  `registry card-products`), onboarding detection from Gmail and the
  bank-dashboard browser pass (suggestion-only, adopted by typing),
  and soft `card_access`/`note` annotations on `afford` transfer
  paths — the Chase/Citi card gates now structured as
  `transfer_partners.json` `card_gate` data annotate confidence,
  never remove a path.
- The AwardWallet Account Access API as the primary balance,
  elite-status, and points-expiration source, with per-program browser
  reads as the fallback lane: the `awardwallet` CLI group (`pull`,
  `providers`, `account`), an `awardwallet` provider-code field on every
  `programs.json`/`banks.json` row surfaced through
  `registry.awardwallet_map()` (24 of 38 entries map;
  aggregator-blocked programs stay on the browser lane), and an
  `awardwallet_op_ref` preferences key. `docs/awardwallet-api.md`
  records the API's observed shapes.
- SerpApi Google Flights as `bridge`'s fallback cash-pricing backend
  (`serp.py`): engaged per airport pair only when fli errors or prices
  nothing and a key resolves, quotes tagged `source: fli|serpapi`,
  keyed by a `serpapi_op_ref` preferences key.
  `docs/serpapi-flights-api.md` records the API.
- Shared secret resolution in `keys.py`: every client resolves its
  credential env-var-first, else the preferences 1Password `op://`
  reference, validated so a key never echoes into an error message or
  rendered traceback.

### Changed
- Cabin fit counts only below-preferred minutes, so a first-class
  segment against a business preference carries zero miss; the fact is
  renamed from `mixed_cabin_minutes` to `below_cabin_minutes`.
- `rank` and `finalize` capture their input fingerprint at work start,
  so a mid-run prefs edit marks the phase stale instead of stamping
  over it; state-conflict (exit 3) messages name the prefs file or
  trips directory they tripped on.
- The empty-front collapse in cost-tier peeling is now an invariant
  assert: an adversarial re-verification of the band/Pareto math (2.1M
  ordered vector pairs, 3,000 fuzz populations, zero violations)
  proved nonnegative costs make every domination strictly lower the L1
  cost sum, so a front stays populated; the old silent collapse would
  have masked mis-tiering.
- `skills/getaway/SKILL.md`, `references/planning.md`, and
  `references/doctrine.md` rewritten for the journey engine: the parse
  table lands preferences/constraints, `## Journeys` and `## Hotels`
  replace the round-trip prose, presentation maps the four finalists
  result classes, and the doctrine supersedes its drifted cc-notes
  records ("Preferences never gate", "The journey is the unit",
  "Ranking runs in lanes", "Lodging derives from observed clocks",
  "Quota is reserved at the boundary", "The contract is the CLI; the
  script is a template").
- Sweeps request all cabins in one call with `include_filtered=true`;
  soft dates pad by seven days while confirmed constraints sweep
  exact; sweep artifacts are JSON envelopes with provenance and
  search states (`sweep run <slug> outbound:<label>|return`);
  shortlist takes `--leg outbound|return` and applies no seat,
  mileage, or cabin gate — pseudo-code feasibility compares
  server-expanded airports recorded on the leg.
- The single-row expand command is now `expand detail <id> --cabin`;
  `expand run <slug>` owns composition.
- Onboarding and refresh gather hotel balances and tiers, mine
  instruments (hotel free-night certificates included) instead of
  credits, and route browser reads by auth class per
  `skills/refresh/gather.md`.
- The CLI project gains the `flights` dependency for `bridge`.
- README pass to the canonical skeleton: prerequisites named in Get
  started, the live board screenshot as the demo, the 1Password
  `op_ref` detail relocated to Requirements, "How it plans" cut to one
  architecture paragraph, and the stale 26-program count caught up to
  the opener's 28.
- A preferences doc predating the `cards` preference is rejected
  loudly and regenerates through onboarding, the same clean cutover
  the v2 loyalty shape took.
- Cash-leg clocks are required end to end: both bridge backends emit
  departure and arrival clocks unconditionally, so stays derive from
  observed arrivals with no unknown-arrival branch.

### Removed
- The `layover_style` and `program_preference` trip preference keys —
  validated, consumed nowhere. A stored trip carrying them fails its
  next edit loudly, the same clean-cutover posture as the rest of v2.
- The `window_overshoot` leg fact: its one consumer moved to
  preference-relative math, and its trip-relative framing disagreed
  with the miss it fed.
- The `return_viability` factor, its evidence collector, and the
  "returns are a second invocation" flow — returns are pipeline data
  in the same dispatch.
- `_verdict_score` and every composite-score residue; the hard
  mileage-ceiling filters (mileage is a target preference; a real
  budget is `constraints.mileage_limit`).
- The 240-minute cash-cabin cutoff (`CASH_CUTOFF_MINUTES`) and its
  doctrine ruling — cabin per cash leg is model judgment fed by
  duration fit facts.
- The separate `hybrids` finalists class — hybrids are journeys.
- The static phase machinery: `PHASE_ARTIFACT_DEPS`,
  `_HYBRID_ONLY_ARTIFACTS`, the v1 walker's fourteen phase schemas,
  its `persist()`/quota-folding bookkeeping, and the prose-maintained
  guard tables the compiled graph replaces.
- `prefs credit-add`/`credit-list`/`credit-remove` (superseded by the
  instruments commands) and the `credits` preference shape.
- Legacy preferences detection: `LEGACY_KEYS`, `_reject_legacy`, and
  every pre-v2 mention — an invalid doc fails ordinary schema
  validation, with no migration story.

### Fixed
- Date-padded rows no longer crowd requested dates off the shortlist,
  and optional-leg variants are never silently starved. The P5
  positioning ask surfaced both: padding rows shared each per-cabin
  cohort with in-window rows and won on price, so only out-of-window
  LAX departures survived selection — window membership now joins the
  cohort interleave as the outermost axis, from the leg's own derived
  window rather than the padded sweep range, so in-window rows claim
  rank-wise budget share before any padding row. And a plan with
  optional legs now discloses per-variant composition accounting
  (`provenance.variants`: chains built, expanded, dropped for
  continuity, and journeys, keyed by included legs), so a variant that
  composed nothing is a visible fact, not an absence; plans without
  optional legs emit byte-identical artifacts.
- The composition beam ranks only feasible chains, and an empty board
  now explains itself. The founding NRT hybrid produced zero journeys
  at the default beam width: all chains ranked purely on miles and
  cash, so the cheapest survivors were date-infeasible cash hops that
  died at the stay gate after crowding out 130 composable journeys —
  and the resulting empty finalists carried no explanation despite 98
  healthy prefix leads. Provably date-infeasible chains — a cash-prior
  boundary whose quote clock breaks the stay or continuity bounds —
  now drop before the beam (disclosed as `date_infeasible`, distinct
  from `beam_cut`; award-prior boundaries keep their post-expansion
  judgment, since a shortlist row's arrival is unknowable before
  expansion), and finalize threads partial-chain leads onto the board
  as `partial_leads`. A cash quote crossing midnight judges by its own
  clock, never its date field.
- Shortlist selection diversifies across cabins, closing the retrieval
  half of the business-drop bug (the v2 rank fix below closed the
  ranking half): `_cohort_select` cohorted only by date and source and
  picked cheapest-mileage-first, so economy filled the per-endpoint
  budget before any business row — the P5 live capstone caught all 203
  composed journeys surfacing economy while direct business award
  space sat in cache. The cohort key now carries cabin with a
  rank-wise interleave, diversity not preference: single-cabin pools
  select identically, and the trade — distinct-date coverage divides
  by cabins present — is tunable per trip through
  `plan.tuning.expansion_budget_per_endpoint`.
- A business-class ask boards business journeys. The canonical
  warm-beachy run produced six economy finalists, each annotated
  "outside your preferred cabin", while all-business round trips sat
  beyond the cut: a primary cabin preference had no reach across cost
  tiers, and `constraints.cabin` was validated but consumed nowhere.
  Fixed engine-side by the clears guard, the priority fold, and
  deterministic notable stretches — pinned by a regression test that
  asserts the business journey boards with and without an assess
  artifact.
- Window fit evidence measured from the wrong bound: a departure
  inside the preferred window read "1 day(s) past", and a late one
  measured its delta from the window start ("5 day(s) past" for 3).
  The miss now reads the preference's own `{start, end}` on both
  sides.
- The plugin manifest declares its cc-present dependency (`>=0.9.1`,
  window-keyed board resolution), so fresh installs pull the boards
  plugin automatically once its marketplace is added — the README
  install steps and requirements now say so.
- The program count reads 28 on every surface; the AGENTS fragment
  and `cli/pyproject.toml` lagged at 26.
- Region pseudo-code origins now expand against both the packaged airport floor and origins observed in search sweeps, preventing valid shortlist rows from being dropped.
- Cached `/search` teaser seat counts no longer masquerade as bookable: sufficiency reads the live-expanded row, and stale cached empties render "unverified" instead of "no space".
- `/trips/{id}` normalization tolerates programs that omit
  `TaxesCurrency` while reporting `TotalTaxes` (observed live on
  `american`): `taxes_currency` is `None` when unreported instead of a
  `KeyError` crashing `expand run`.

## [1.0.0] - 2026-07-13

The v2 rewrite: the planning engine moves from a shell script plus prose
doctrine into a Python CLI with durable, checkpointed state, and every
surface — skill, workflow, hooks, onboarding — rides it.

### Added
- The `getaway` CLI, a uv project at `cli/`: `prefs`, `trip`, the
  seats.aero calls (`search`, `availability`, `routes`, `expand`),
  `sweep`, `shortlist`, `rank`/`afford`/`quality`, `registry`,
  `learnings`, `quota`, and `cache` groups, with typed exit codes and
  flock-guarded atomic writes.
- Hybrid JSON+SQLite state under `~/.getaway`: each trip owns
  `trips/<slug>/` — `trip.json` memory, sweep/shortlist/evidence/finalist
  artifacts, and `checkpoints.json` stamping every phase with input
  fingerprints and a TTL — beside a WAL `cache.db` of every availability
  row ever fetched. A killed or resumed session re-invokes the same
  workflow and every fresh phase skips wholesale, spending zero quota;
  editing the window or an avoid list invalidates exactly the dependent
  phases.
- 12-factor per-trip judgment engine: a packaged factor registry
  (`registry factors`), `trip profile` deriving per-factor tiers from
  the ask and prefs, and `rank` enforcing mileage-band tier discipline —
  primary factors reorder, secondary break ties, notes annotate, and
  `barely` seat products demote in-band, so mileage stays dominant.
  Rank prices each finalist at its expanded bookable mileage, not the
  cached sweep row. Layover verdicts — duration bands plus how
  interesting the layover city is, with `prefer_cities`/`avoid_cities`
  from the new `layovers` preference — ride the engine as the
  `layovers` factor, and each finalist carries one evidence line per
  active factor.
- Packaged data registries at `cli/getaway/data/`, surfaced by the
  `registry` and `quality` commands: programs (26, plus `british` and
  `iberia` in beta), banks, transfer partners, seat quality, regions
  (the undocumented `QAF` included), factors, cabins, and continents —
  plus researched, source-noted status-earning and points-pricing
  datasets.
- Trip credits and status goals: `credits` (airline eCredits, vouchers,
  companion certificates, with expiry filtering and
  `credit-add`/`credit-list`/`credit-remove`) and `status_goals`
  preferences, new onboarding form sections, a Gmail credits-mining flow
  in `gather.md`, and the `status_earning`, `points_purchase`, and
  `trip_credits` factors that consume them.
- `plan-trip.js` v2: slug-first `{project, slug}` args, ten phases —
  load, sweep, shortlist, onward, bridge, expand, evidence, assess,
  rank, finalize — branching only on trip status, per-label sweep
  fresh-skip, per-collector evidence resume, and quota-floor gating.
- Test suite: 268 Python tests over the CLI (respx-mocked seats.aero
  boundary) and 7 `node --test` workflow cases via
  `tests/workflow/harness.mjs`.
- `/trips/{id}` timestamp semantics — local wall-clock stamps with a
  spurious trailing `Z`, minute durations — in `docs/seats-aero-api.md`,
  verified live 2026-07-13; the CLI's `expand` applies the cleanup and
  derives per-connection layover minutes.

### Changed
- `skills/getaway/SKILL.md` slims from 950 to 112 lines: an engine
  contract plus routing table, with the planning doctrine moved to
  `skills/getaway/references/planning.md` and the settled rulings to
  `references/doctrine.md`; jq recipes and hand-kept region tables are
  replaced by CLI and registry commands.
- `skills/refresh/gather.md` derives its Gmail sender lists, browser
  host lists, and credit issuer slugs from `registry programs --domains`
  and `registry banks` instead of hand-kept domain tables.
- Onboarding and refresh write per-record through `prefs set-balance`,
  `set-status`, and `credit-add`, and refresh flags credits expiring
  within 90 days.
- Hooks run on the CLI: `onboard.py` shells out to `getaway prefs
  status`, and `reflect.py` routes durable facts to `prefs`, trip facts
  to `trip set` under `~/.getaway/trips`, and API quirks to
  `learnings add`.
- capt-hook wiring is attach-only: the `captain-hook` plugin is now a
  declared dependency (`>=9.9.0`) that dispatches hook events, and
  `hooks/hooks.json` ships only the canonical `pack attach` entry. Add the
  `yasyf/captain-hook` marketplace before installing getaway; existing users
  re-run `claude plugin install` after adding it (see the README).

### Removed
- `skills/getaway/getaway.sh` — every subcommand has a CLI home.
- `skills/getaway/seat-quality.md` and
  `skills/getaway/transfer-partners.md` — the verdict table and transfer
  map now ship as packaged data behind `quality` and
  `registry transfer-partners`.
- The free-form `~/.getaway/learnings.md` — learnings are append-only
  JSONL through `learnings add`/`learnings list`, scoped `api`, `prefs`,
  or `general`.
- The `~/.getaway/plans/<slug>.json` layout — trips live at
  `~/.getaway/trips/<slug>/`. Migration is a one-time manual move; no
  migration code ships, per the styleguide's rule against
  backwards-compat layers.

## [0.8.0] - 2026-07-13

### Added
- `## Affordability and top-ups` in the getaway skill: per-finalist
  shortfall math from trip integers, transfer-first suggestions from the
  new bank map, a judgment-guided "buy N points ≈ $X" estimate citing
  the rate's source and date, a "Points check" section block beside the
  picker, and an affordability line per delivered leg.
- `skills/getaway/transfer-partners.md`: the static bank-to-program
  transfer map — each bank's partners among the 26 programs with ratios
  and quirks, plus the 11 programs no bank reaches (verified
  2026-07-12).
- `## Seat quality` in the getaway skill: business hard products join
  the ranking — `barely` products (old BA Club World and its yin-yang
  kin, 7-across flats, anything angled) soft-demote below every true
  lie-flat, unknowns rank neutral, the verdict rates the longest
  business segment, mixed fleets resolve by `WebSearch`, and every
  business finalist carries a product note with `barely` phrased as a
  warning.
- `skills/getaway/seat-quality.md`: the static carrier + aircraft
  verdict table — `suite` / `solid` / `dated` / `barely` with Verify
  marks on mid-retrofit fleets (verified 2026-07-12).
- `## Routing strategies` in the getaway skill: a trip is a
  composition of legs — gateway hybrids (a lie-flat award to a hub,
  a cash hop onward), open jaws, two-award stitches, and long-range
  positioning all price beside direct awards — with the cash-cabin
  default (onward legs under ~4 hours book economy, longer book
  business) and per-program gateway sets refined via `routes`.
- `routes` subcommand in `getaway.sh`: wraps `GET /routes` for one
  program, with client-side `--origin-region`/`--dest-region` jq
  filters, emitting JSONL one route per line.
- `hybrid` input to `plan-trip.js`: gateways, onward destinations,
  and the cash-cabin cutoff drive two new phases — Onward, one
  all-program award sweep from the gateways to the onward
  destinations, and Bridge, fli cash quotes per gateway pair — and
  the return gains a `hybrids` list of gateway-cash and two-award
  candidates beside the direct finalists.
- `documents` preference — free-text `passports`, `residency`, and
  `visas` arrays: onboarding collects them in a new form section with
  Gmail immigration-sender signals as suggestions, Enrich visa notes
  address the actual traveler instead of "a US passport holder", and a
  zero-quota Transit pass flags same-ticket connections that may need
  a transit visa and self-transfer gateways' entry requirements —
  flag, never filter.

### Changed
- The browser read rides agent-browser-with-cookies 0.12.0: each
  gatherer seeds its own session with `abwc-seed --session <slug>` and
  carries the override on every `ab` call, `ab close` included —
  cookies plus web storage in one seed, replacing the raw
  `agent-browser --session` + `cookiesync cookies` pull.
- Points balances bias planning instead of gating it: per-program sweeps
  cover all programs ordered funded-first, `sources` narrows only on an
  explicit user ask and never derives from balances, and unfunded
  finalists stay on the shortlist with an affordability annotation.
- Africa sweeps ride `QAF` on `/search` — an undocumented pseudo-code,
  verified live 2026-07-12 with expansion observed across CMN, CAI,
  ADD, CPT, JNB, and NBO — demoting the per-program
  `--dest-region Africa` sweep to a fallback; the "No Africa
  pseudo-code exists" claim leaves the skill and API doc.
- Business plans in `plan-trip.js` expand a ~1.5× finalist buffer,
  classify each trip's longest business segment against
  `seat-quality.md` during Expand, resolve Verify-marked fleets in a
  new zero-quota Verify phase, and re-rank by (soft avoid, `barely`
  product, mileage) before truncating; the low-quota path drops the
  buffer first.
- `## Positioning flights` folds into `## Routing strategies` as its
  `### Cash positioning` subsection; the doctrine covers every lever,
  not just the home-to-origin hop.
- The planning workflow composes hybrid routings and compares them
  against direct awards on total cost — miles, taxes, and cash
  onward per finalist — on every region- or vibe-scale ask.
- Expand takes a per-row cabin: stitched onward award legs expand at
  their own cabin instead of the trip-wide one.

## [0.7.0] - 2026-07-12

### Fixed
- The gather doctrine no longer attributes a logged-out airline landing
  to IndexedDB auth — an untested inference from a hung session; the
  branch is now neutral: a page that lands logged-out after a
  verified-fresh login is noted and skipped.

### Added
- `skills/getaway/plan-trip.js`: a shipped Workflow script that runs the
  planning pipeline — sweep, shortlist, expand, enrich — as parallel
  agents; the planning workflow invokes it for any ask spanning two or
  more destination buckets or programs.
- Dedicated `/getaway:onboard` skill: first-run setup where parallel Gmail
  and airline/bank-login gatherers seed a cc-present form; nothing is
  written until Submit.
- Dedicated `/getaway:refresh` skill: re-reads saved award balances and
  elite statuses on demand from logged-in airline and bank sites — Amex,
  Chase, Citi, and Capital One transferable points included — falling
  back to a Gmail statement scan per bank host that fails, and writing
  results through `prefs-set` directly.
- `skills/refresh/gather.md`: the shared source for the 26-program and
  bank domain tables and the browser-read and Gmail lockdown gather
  procedures.

### Changed
- The browser read fans out per host: one main-level `cookiesync auth`
  priming tap whose `--reason` names every host, then one gatherer
  subagent per host in its own `agent-browser` session — the shared
  per-session grant keeps the fan-out at one tap, and a hung or
  logged-out host no longer stalls the rest. Replaces the single
  serial-site-walk subagent.
- Flows fan out by default: a new SKILL.md `## Orchestration` ladder
  (batch, then parallel subagents, then the shipped workflow, then a
  team for multi-round multi-city plans), onboarding gatherers spawned
  as parallel subagents, and parallel trip expansions, enrichment, and
  positioning quotes.
- The `getaway` skill slims to planning: the onboarding and
  balance-refresh trigger phrases move to the new skills' descriptions.
- Hooks re-aimed: the onboarding nudge points unconfigured sessions at
  `getaway:onboard`, and session reflection also fires for onboard and
  refresh sessions.
- Onboarding's home-airport inference is calendar-first: ten years of
  Google Calendar's Gmail-auto-extracted flight events (locked-down
  `gog calendar events --event-types from-gmail`, tallied in the jq
  pipe) with a frequency-plus-margin rule — at least 10 segments and
  twice the runner-up — before naming `home_airport`; the Gmail
  fallback becomes a sender-domain query over the program-domain table
  with a dedicated 25-body budget, replacing the subject-phrase query
  that counted return legs as origins. Saved preferences always win
  form placeholders — a discovery is a label suffix naming source and
  strength, and blank keeps the saved value — and airport fields
  accept seats.aero region pseudo-codes (QBA, WST, NYC…): home and
  origin airports store them verbatim, avoid-transit expands them to
  member airports at save.

## [0.6.0] - 2026-07-11

### Added
- Per-trip memory at `~/.getaway/plans/<slug>.json`, with the active slug
  in `~/.getaway/plans/current` and five `getaway.sh` subcommands:
  `plan-new`, `plan-set`, `plan-show`, `plan-list`, and `plan-done`.
  Trip-shaped constraints — window, cabin, party, regions, vibe,
  `avoid_final_destinations` (final-stop veto only; those airports stay
  valid as connections), and a `decisions` log — write back mid-planning,
  the moment each one is pinned down.
- `avoid_transit` preference: airports never to connect through, enforced
  against `/trips/{id}` segments.
- Three-way session reflection: always-true facts route to preferences,
  trip-scoped facts to the active plan via `plan-set`, and skill or API
  corrections to the docs.

### Changed
- Preferences hold only always-true profile facts. `cabin`,
  `trip_length_days`, `departure_days`, and `avoid_destinations` were
  trip-shaped, not global; they leave `~/.getaway/preferences.json` for
  trip memory. Schema files carry no version field — validation is
  shape-only, and a file that doesn't fit is re-initialized, not
  migrated.

## [0.5.0] - 2026-07-11

### Added
- cc-present block pack at `.claude/components`, installed with the plugin:
  `getaway.itinerary` (one award option — segments, integer miles,
  minor-unit taxes plus currency, remaining seats, booking link, and
  `UpdatedAt` freshness), `getaway.flight` (a single leg, with an optional
  cash price for positioning flights), `getaway.availability` (interactive
  date × cabin grid; a tap submits `{date, cabin}`), and
  `getaway.option-picker` (interactive shortlist; a tap submits
  `{optionId}`).

### Changed
- Presenting options now composes the pack blocks — an option picker for
  the shortlist and an itinerary card per expanded finalist — instead of
  prose card titles with per-card approval blocks.

## [0.4.0] - 2026-07-11

### Added
- Auto-filled onboarding. When the user accepts the form, the skill first
  scans Gmail read-only through `gog` (lockdown flags on every call) for
  programs, statuses, balances, and the home airport, then reads live
  balances and elite tiers from airline sites via the
  `agent-browser-with-cookies` skill behind one Touch ID tap. Discoveries
  seed the form; nothing is written until Submit.
- `statuses` preference: program slug to elite tier, breaking mileage-cost
  ties toward carriers where the user holds status.
- "Refresh my balances" path: re-scrape the airline sites for the programs
  already on file, merge the results with `prefs-set`, and report per-program
  deltas — no form round-trip.

### Changed
- The onboarding nudge hook now mentions the auto-fill step, and the
  capt-hook pack manifest covers both shipped hooks.

## [0.3.0] - 2026-07-10

### Added
- First-run onboarding for the `getaway` skill. The skill offers a
  cc-present preferences form before planning a trip and writes submitted
  answers through the new `getaway.sh` `prefs-status` and `prefs-set`
  subcommands.
- Plugin-shipped capt-hook PostToolUse nudge that backstops onboarding.
  When a session loads the skill while `~/.getaway/preferences.json` is
  missing or records no balances, it advises offering the form without
  blocking.

## [0.2.0] - 2026-07-10

### Added
- Award-trip planning workflow in the `getaway` skill: preference-driven
  region sweeps, offline jq filtering, trip expansion with real taxes and
  booking links, and interactive approval rounds.
- `skills/getaway/getaway.sh` helper with `prefs-init`, `prefs`, `search`,
  `availability`, `trip`, and `quota` subcommands over the Partner API,
  including cursor+skip pagination with cross-page dedupe by `ID`.
- Preferences file at `~/.getaway/preferences.json`: home airport, cabin,
  avoided destinations and airlines, per-program balances, and an `op_ref`
  1Password reference as the API-key fallback.
- Plugin-shipped capt-hook Stop reflection hook that sweeps each getaway
  session for durable learnings and routes them to the preferences file or
  the skill docs.
- Region pseudo-code reference in `docs/seats-aero-api.md`, verified live
  against `/search` on 2026-07-10, plus the observed pagination sentinel and
  `/trips/{id}` response envelope.
- Initial scaffolding.
- Claude Code plugin skeleton: `getaway` plugin manifest, in-repo marketplace,
  and the `getaway` skill with seats.aero auth and a cached-search smoke call.
- seats.aero Partner API reference at `docs/seats-aero-api.md`.

[Unreleased]: https://github.com/yasyf/getaway/commits/main
