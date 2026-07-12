---
name: onboard
description: Sets up getaway travel preferences. Triggers when the user wants to set up getaway ("set up getaway", "set up my travel preferences", "run getaway onboarding") or to record airports, points balances, elite statuses, or avoid lists for award planning. Auto-fills from Gmail and logged-in airline and bank sites; nothing is written until the form's Submit. Refreshing balances already on file is /getaway:refresh.
allowed-tools: Bash(jq:*), Bash(op:*), Bash(gog:*), Agent
---

# onboard

Onboarding collects the user's airports, balances, elite statuses, and
avoid lists and writes them in one pass. It is optional: the user may
skip it and plan on the shipped defaults.

When the user accepts onboarding, run auto-fill immediately — announce
each step, do not ask permission for it. Start at the main level with
Gmail query 1, the domain tally: the browser gatherer's host list
derives from it, and the mailbox question below is asked here, before
any spawn. Then run the two gatherers below as parallel subagents —
one message, two Agent calls, Gmail (queries 2–4 and body fetches)
beside airline logins. The gatherers degrade independently: skipping
one costs nothing but its answers, and neither writes a byte. The
form's Submit is the sole write gate, and the form is never delegated.

## Auto-fill from Gmail

This section is the Gmail subagent's brief. Spawn it with the chosen
account and the tally-narrowed `from:` list; it returns
`{programs, statuses, balances, home_airport, origin_candidates}` as
JSON. Query 1 runs at the main level first, so the tally can seed both
gatherers.

Invocation mechanics — gog detection, the five lockdown flags, and the
trust doctrine — live in gather.md's
[Gmail read](../refresh/gather.md#gmail-read-gog-lockdown); Read
`${CLAUDE_PLUGIN_ROOT}/skills/refresh/gather.md` first.

Resolve the account per gather.md's account rule (`gog auth list
--json`; ask when more than one is configured, never guess). In
onboarding the mailbox pick happens at the main level before the
gatherers spawn — the lone question in this flow.

Run four headers-first queries, fetching at most 10 message bodies
total across all four per gather.md's body-fetch rule:

1. **Programs, airlines, and banks** — `from:(<the 26 program domains
   and the 4 bank domains>) newer_than:1y`, `--max 100`, the domains
   from both tables in gather.md's
   [Program and bank domains](../refresh/gather.md#program-and-bank-domains).
   Tally sender domains with `jq`: the heavy hitters are the frequent
   airlines and the candidate programs, and bank senders with hits join
   the browser host list. This query is the main-level pre-spawn step;
   the remaining three run inside the subagent.
2. **Status and balances** — the tally-narrowed `from:` list plus
   `subject:(status OR elite OR tier OR statement OR balance OR "miles
   summary") newer_than:1y`, `--max 25`. Take tier strings verbatim;
   parse balances to integers, most recent email wins.
3. **Home airport** — `subject:("your itinerary" OR "flight
   confirmation" OR "booking confirmation" OR "e-ticket" OR "boarding
   pass") newer_than:2y`, `--max 50`. The mode of first-segment
   departure airports is the home airport; runners-up are
   `origin_airports` candidates.
4. **Bank points** — the query and its parse rules live in gather.md's
   [Gmail read](../refresh/gather.md#gmail-read-gog-lockdown); senders
   map to `balances.transferable` keys through its bank table.

## Balances from logins

This browser read is the second parallel subagent, spawned beside the
Gmail gatherer with the host list fixed at spawn time; it returns
`[{slug, balance, tier}]`, and the Touch ID prompt reaches the user
from a subagent all the same. Programs the Gmail gatherer surfaces
after the spawn enter the form as Gmail-sourced placeholders — offer a
second browser pass only when the user wants exact numbers.

Derive the host list automatically: the Gmail-tally programs and
banks, any programs or banks the user has named, and the keys already
in `balances.programs`, `statuses`, and `balances.transferable` — banks
ride in the same cookie session — mapped to login domains through the
tables in gather.md's
[Program and bank domains](../refresh/gather.md#program-and-bank-domains).
The mechanics — one cookie pull, the Touch ID reason, per-site
extraction, the failure branches — are gather.md's
[Browser read](../refresh/gather.md#browser-read).

## Confirm in the form

Collect the answers with a cc-present form, not the approval board. Seed
each field's `placeholder` with the user's current preference (from
`prefs`); the shipped defaults below stand in when no file exists yet. A
field auto-fill discovered gets the discovered value as its placeholder
instead, plus a label suffix naming the source — `— found in Gmail,
blank keeps it` or `— read from united.com`.
This document passes `cc-present push --dry-run`:

```json
{
  "version": 1,
  "title": "getaway onboarding",
  "intro": "Set your award-travel preferences. Anything you leave blank keeps the value shown as its placeholder. Press Submit when done.",
  "submit": { "label": "Save preferences", "note": "Writes the values below to ~/.getaway/preferences.json." },
  "blocks": [
    { "id": "sec-airports", "type": "section", "title": "Airports" },
    { "id": "home-airport", "type": "input", "label": "Home airport (IATA)", "placeholder": "SFO" },
    { "id": "origin-airports", "type": "input", "label": "Origin airports to search from (comma-separated IATA)", "placeholder": "SFO,SJC,SAN,PDX,DEN,LAS,SLC,YVR" },
    { "id": "sec-avoid", "type": "section", "title": "Avoid" },
    { "id": "avoid-transit", "type": "input", "label": "Airports you never want to connect through, comma-separated IATA", "placeholder": "none" },
    { "id": "avoid-airlines", "type": "input", "label": "Airlines to avoid — name:soft or name:hard, comma-separated", "placeholder": "Ethiopian:soft", "multiline": true },
    { "id": "sec-balances", "type": "section", "title": "Mileage balances", "md": "List every program you hold. Format: program:points, comma-separated." },
    { "id": "balances-programs", "type": "input", "label": "Airline programs (program:points, comma-separated)", "placeholder": "aeroplan:88000, alaska:90000", "multiline": true },
    { "id": "balances-transferable", "type": "input", "label": "Transferable points (bank:points, comma-separated)", "placeholder": "amex:150000, chase:80000", "multiline": true },
    { "id": "statuses", "type": "input", "label": "Elite status (program:tier, comma-separated)", "placeholder": "united:1K, alaska:MVP Gold 75K", "multiline": true },
    { "id": "sec-auth", "type": "section", "title": "seats.aero API key" },
    { "id": "op-ref", "type": "input", "label": "1Password reference for the seats.aero API key", "placeholder": "op://Vault/item/field" }
  ]
}
```

Drive it with `Skill(cc-present:present)` exactly like
[Presenting options](../getaway/SKILL.md#presenting-options) — push, rounds, submit,
outcomes, close are the same loop.

Reading the outcomes back takes judgment; the form's free-text fields and
the preference schema differ:

- `input` blocks carry no seeded value — the placeholder displays what
  blank keeps. A field absent from the outcomes means the user left it
  blank: keep the placeholder's value. On an ordinary field that is the
  current preference, so omit the key from the patch; on a
  discovery-seeded field it is the discovered value, so include it in
  the patch. Never overwrite a preference with an empty value.
- `avoid-transit` answers are comma-separated IATA codes; split them into
  the `avoid_transit` array. A blank field keeps the current list, so omit
  the key; a literal `none` clears it, so send `"avoid_transit": []`.
- `avoid-airlines` answers are `name:soft|hard`, but the `avoid_airlines`
  preference stores `{code, name, strength}` objects matched on the IATA
  `code`. Resolve each airline name to its code yourself (Ethiopian is
  ET) and build the full object.
- Balance answers are `program:points` free text. Parse the points to
  integers; resolve program names to seats.aero source slugs (Alaska is
  `alaska`, Aeroplan is `aeroplan`) for `balances.programs` and bank
  names (`amex`, `chase`, `citi`, `capitalone`) for
  `balances.transferable`. Always send both maps, merged with the current
  values — the top-level merge replaces `balances` whole, so a patch
  carrying only one map erases the other.
- Status answers are `program:tier` free text. Resolve program names to
  slugs the same way and keep the tier string verbatim (`1K`,
  `MVP Gold 75K`). The merge warning applies here too: the patch
  replaces the whole `statuses` map, so always send it merged with the
  current values.

Write the patch with `prefs-set`. The merge is top-level: each key in the
patch replaces that whole key, and every omitted key keeps its current
value (the shipped defaults when the file does not exist yet). A real
write:

```bash
"${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs-set <<'JSON'
{"home_airport": "SFO",
 "avoid_airlines": [{"code": "ET", "name": "Ethiopian Airlines", "strength": "soft"}],
 "statuses": {"united": "1K"},
 "balances": {"programs": {"aeroplan": 88000, "alaska": 90000},
              "transferable": {"amex": 150000, "chase": 80000}},
 "op_ref": "op://Vault/item/field"}
JSON
```

```
/Users/<user>/.getaway/preferences.json
```

`prefs-status` flips to `configured` once a balance lands. Close by
running `prefs` and confirming the saved values with the user.
