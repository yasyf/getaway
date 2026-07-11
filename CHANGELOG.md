# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
