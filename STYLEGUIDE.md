# getaway Style Guide

The concrete style rules for this repository.

## Core Principles

1. **Fail fast, fail loud.** No defensive coding: no fallbacks, shims, or
   backwards-compat layers, and no guards against impossible states. No sentinel
   values, no silent defaults. If unused, delete it. Crash on the unexpected.
2. **Make invalid states unrepresentable.** Branded/newtype primitives, immutable
   data structures, required fields over optionals.
3. **Minimal changes.** Stay within scope. Make the test pass, then stop. Improve
   only the code you touch.
4. **Match surrounding code.** Follow this guide first, then the file you're in,
   then the module. If surrounding code violates this guide, fix it.

## Skill & Manifest Conventions

This repo ships a Claude Code plugin: the "code" is SKILL.md prose, JSON
manifests, and the shell samples inside skills.

1. **One skill per directory** under `skills/`, and the frontmatter `name`
   matches the directory name. Helper scripts live beside the SKILL.md that
   uses them.
2. **Descriptions are triggers.** A skill's frontmatter `description` names
   the concrete user asks that should activate it, not what the skill "is".
   - Good: `Triggers when the user wants to plan an award flight or trip on points or miles.`
   - Bad: `A skill for award travel.`
3. **One fragment, every surface.** The README opener fragment, GitHub About,
   `plugin.json` description, and `marketplace.json` description stay
   identical. Change one, change all.
4. **Secrets come from the environment.** Shell samples read keys like
   `SEATS_AERO_API_KEY` from env or the gitignored `.env`; a literal key never
   appears in a file, sample, or transcript.
   - Good: `-H "Partner-Authorization: $SEATS_AERO_API_KEY"`
   - Bad: `-H "Partner-Authorization: pro_abc123..."`
5. **curl calls fail loud**: `-fsS` so HTTP errors surface instead of parsing
   error bodies as data.
6. **Prose passes slop-cop.** Run `slop-cop check <file> --lang=markdown` on
   any SKILL.md or doc you touch and fix the genuine tells.

## Error Handling

Keep error-handling blocks minimal: only the operation that can fail belongs
inside. No catch-all handlers that swallow everything; use dedicated error types.
Read required configuration so a missing key fails at startup. No sentinel return
values; raise, or return a typed result.

## Code Organization

Order each module: imports, constants, type aliases, helpers, classes, then
functions. Constants sit immediately after imports, before any class or function.
Use the language's export-control mechanism instead of underscore/naming
conventions to hide internals.

## Comments & Docstrings

Comments are terse and used sparingly — the code documents itself through names, types,
and organization. The one exception is documentation-generation comments: the doc
comments your language's doc tool renders for the public API, each a real description
rather than a restatement of the signature. Beyond those, comment only for TODOs,
non-obvious workarounds, or disabled code.

## Testing

Write strict assertions against specific expected values; a test that can't fail
uncovers nothing. Mock the boundaries your code talks to, such as the network,
filesystem, and clock, and leave the function under test real. A database (or any
stateful service) is not a mock boundary: when a test needs one, start a real
ephemeral instance with testcontainers rather than mocking the driver or using an
in-memory fake. Parameterize repeated test bodies, giving each case a descriptive
id and its own expected values.
