# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
