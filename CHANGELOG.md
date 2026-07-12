# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
