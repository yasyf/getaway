---
name: onboard
description: Sets up getaway travel preferences. Triggers when the user wants to set up getaway ("set up getaway", "set up my travel preferences", "run getaway onboarding") or to record airports, points balances, elite statuses, travel documents (passports, residency, standing visas), or avoid lists for award planning. Auto-fills from Gmail and logged-in airline and bank sites; nothing is written until the form's Submit. Refreshing balances already on file is /getaway:refresh.
allowed-tools: Bash(jq:*), Bash(op:*), Bash(gog:*), Bash(cookiesync:*), Agent
---

# onboard

Onboarding collects the user's airports, balances, elite statuses,
travel documents — passports, residency, standing visas — and avoid
lists and writes them in one pass. It is optional: the user may
skip it and plan on the shipped defaults.

When the user accepts onboarding, run auto-fill immediately — announce
each step, do not ask permission for it. Start at the main level with
Gmail query 1, the domain tally: the browser gatherer's host list
derives from it, and the mailbox question below is asked here, before
any spawn. Prime the cookie grant at the main level per gather.md's
[browser read](../refresh/gather.md#browser-read) — Touch ID denied
means the spawn goes Gmail-only, no browser gatherers. Then run the
gatherers below as parallel subagents in one spawn message — the
Gmail gatherer (reads 2–5, the calendar read among them, and body
fetches) beside one browser gatherer per host. The gatherers degrade
independently: skipping one costs nothing but its answers, and none of
them writes a byte. The form's Submit is the sole write gate, and the
form is never delegated.

## Auto-fill from Gmail

This section is the Gmail subagent's brief. Spawn it with the chosen
account and the tally-narrowed `from:` list; it returns
`{programs, statuses, balances, home_airport, origin_candidates,
document_signals, notes}` as JSON. Query 1 runs at the main level first, so the tally can seed both
gatherers.

Invocation mechanics — gog detection, the lockdown flags, and the
trust doctrine — live in gather.md's
[Gmail read](../refresh/gather.md#gmail-read-gog-lockdown) and
[Calendar read](../refresh/gather.md#calendar-read-gog-lockdown); Read
`${CLAUDE_PLUGIN_ROOT}/skills/refresh/gather.md` first.

Resolve the account per gather.md's account rule (`gog auth list
--json`; ask when more than one is configured, never guess). In
onboarding the mailbox pick happens at the main level before the
gatherers spawn — the lone pre-spawn question in this flow.

Run the five reads below — four headers-first Gmail queries and one
calendar read — fetching at most 10 message bodies total across the
Gmail queries per gather.md's body-fetch rule (the flight-history
fallback carries its own 25-body budget):

1. **Programs, airlines, and banks** — `from:(<the 26 program domains
   and the 4 bank domains>) newer_than:1y`, `--max 100`, the domains
   from both tables in gather.md's
   [Program and bank domains](../refresh/gather.md#program-and-bank-domains).
   Tally sender domains with `jq`: the heavy hitters are the frequent
   airlines and the candidate programs, and bank senders with hits join
   the browser host list. This query is the main-level pre-spawn step;
   the remaining four run inside the subagent.
2. **Status and balances** — the tally-narrowed `from:` list plus
   `subject:(status OR elite OR tier OR statement OR balance OR "miles
   summary") newer_than:1y`, `--max 25`. Take tier strings verbatim;
   parse balances to integers, most recent email wins.
3. **Home airport** — a calendar read, not a Gmail query: ten years
   of Google Calendar's Gmail-auto-extracted flight events, each
   carrying the departure airport as the trailing IATA token of its
   `location`. The invocation and the jq tally live in gather.md's
   [Calendar read](../refresh/gather.md#calendar-read-gog-lockdown);
   project and tally in the pipe so the raw event list never enters
   context. The most frequent departure airport is `home_airport` only
   when it has at least 10 segments and at least twice the runner-up's
   count, and the runners-up in frequency order are
   `origin_candidates`. Short of either margin, return
   `home_airport: null` with `origin_candidates` as the full
   frequency-ordered list, leader included, and the top counts and
   failed margin in `notes` — the orchestrator phrases the label
   suffix from them ("— Calendar suggests YVR, weak: 2 of 3
   segments"). Calendar scope missing or denied, or fewer than 10
   parsed departure segments in the tally (0 is a normal path — Gmail
   auto-extraction can be off): run the flight-history fallback below.
4. **Bank points** — the query and its parse rules live in gather.md's
   [Gmail read](../refresh/gather.md#gmail-read-gog-lockdown); senders
   map to `balances.transferable` keys through its bank table.
5. **Travel-document signals** — headers only, zero body fetches:
   `from:(uscis.dhs.gov OR ttp.cbp.dhs.gov OR travel.state.gov OR
   cbp.dhs.gov OR canada.ca OR cic.gc.ca) subject:(passport OR visa OR
   "green card" OR "permanent resident" OR naturalization OR "Global
   Entry" OR NEXUS) newer_than:5y`, `--max 50`. Tally with `jq` into
   `document_signals` as `[{domain, hits, hint}]` — sender domain,
   message count, and the strongest subject keyword as the hint. These
   are suggestion material only: they reach the form's document fields
   as label suffixes ("— Gmail: 6 uscis.dhs.gov mails, 'green card'
   subjects"), never as adopted values.

The flight-history fallback: `from:(<the 26 program domains from
gather.md's
[Program and bank domains](../refresh/gather.md#program-and-bank-domains)>)
(itinerary OR receipt OR confirmation OR eticket OR "e-ticket" OR
"boarding pass" OR reservation) newer_than:10y`, paged with `--all`
and tallied with jq — the raw message list never enters context
either. Then spend up to 25 sanitized body fetches on this question
alone — a dedicated budget, separate from the shared 10 — stratified
across years and airlines, preferring PNR-bearing transactional
subjects ("eTicket Itinerary and Receipt", "Your Flight Receipt",
"booking confirmation"). Departure airports come from the bodies'
"City (XXX)" patterns. Dedupe by PNR plus flight number and date
before tallying — one physical flight can arrive as confirmation,
eTicket receipt, boarding pass, and schedule change; the same
frequency-and-margin rule then decides `home_airport` and
`origin_candidates`.

## Balances from logins

The browser read fans out per host: after the main-level priming tap,
spawn one browser gatherer per host beside the Gmail gatherer — all in
one message, with the host list derived and fixed at spawn time. Each
gatherer returns one `{slug, balance, tier}` record or a skip note;
the orchestrator aggregates them. Programs the Gmail gatherer surfaces
after the spawn reach the form as Gmail-sourced label suffixes — offer
a second browser pass only when the user wants exact numbers.

Derive the host list automatically: the Gmail-tally programs and
banks, any programs or banks the user has named, and the keys already
in `balances.programs`, `statuses`, and `balances.transferable` —
banks need no special casing, since each host gets its own gatherer
and session and the shared per-session grant keeps the whole fan-out
at one tap — mapped to login domains through the tables in gather.md's
[Program and bank domains](../refresh/gather.md#program-and-bank-domains).
The mechanics — the priming `auth`, the per-host cookie pulls,
per-site extraction, the failure branches — are gather.md's
[Browser read](../refresh/gather.md#browser-read).

## Confirm in the form

Collect the answers with a cc-present form, not the approval board. Seed
each field's `placeholder` with the user's current preference (from
`prefs`); the shipped defaults below stand in only when no preferences
file exists yet. The saved preference always wins the placeholder — a
discovery never displaces it. Auto-fill discoveries appear only as a
label suffix naming the source and its strength — `— Calendar suggests
YVR, weak: 2 of 3 segments` or `— united.com reads 88,000` — never as
the keep-on-blank value; accepting one means typing it.
This document passes `cc-present push --dry-run`:

```json
{
  "version": 1,
  "title": "getaway onboarding",
  "intro": "Set your award-travel preferences. Anything you leave blank keeps the value shown as its placeholder. Press Submit when done.",
  "submit": { "label": "Save preferences", "note": "Writes the values below to ~/.getaway/preferences.json." },
  "blocks": [
    { "id": "sec-airports", "type": "section", "title": "Airports" },
    { "id": "home-airport", "type": "input", "label": "Home airport (IATA or seats.aero region code — QBA, WST, NYC…)", "placeholder": "SFO" },
    { "id": "origin-airports", "type": "input", "label": "Origin airports to search from (comma-separated IATA or seats.aero region codes — QBA, WST, NYC…)", "placeholder": "SFO,SJC,SAN,PDX,DEN,LAS,SLC,YVR" },
    { "id": "sec-avoid", "type": "section", "title": "Avoid" },
    { "id": "avoid-transit", "type": "input", "label": "Airports you never want to connect through — comma-separated IATA or seats.aero region codes; a region code expands to its airports on save", "placeholder": "none" },
    { "id": "avoid-airlines", "type": "input", "label": "Airlines to avoid — name:soft or name:hard, comma-separated", "placeholder": "Ethiopian:soft", "multiline": true },
    { "id": "sec-balances", "type": "section", "title": "Mileage balances", "md": "List every program you hold. Format: program:points, comma-separated." },
    { "id": "balances-programs", "type": "input", "label": "Airline programs (program:points, comma-separated)", "placeholder": "aeroplan:88000, alaska:90000", "multiline": true },
    { "id": "balances-transferable", "type": "input", "label": "Transferable points (bank:points, comma-separated)", "placeholder": "amex:150000, chase:80000", "multiline": true },
    { "id": "statuses", "type": "input", "label": "Elite status (program:tier, comma-separated)", "placeholder": "united:1K, alaska:MVP Gold 75K", "multiline": true },
    { "id": "sec-documents", "type": "section", "title": "Travel documents" },
    { "id": "documents-passports", "type": "input", "label": "Passports held (countries, comma-separated)", "placeholder": "none" },
    { "id": "documents-residency", "type": "input", "label": "Residency and long-stay permits (comma-separated — US green card, UK ILR…)", "placeholder": "none" },
    { "id": "documents-visas", "type": "input", "label": "Standing visas (comma-separated — US B1/B2 to 2030…)", "placeholder": "none" },
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
  blank: keep the placeholder's value — the current preference, or the
  shipped default when no file exists — so omit the key from the patch.
  A blank never adopts a discovery; adopting one takes a typed answer.
  Never overwrite a preference with an empty value.
- Airport answers accept 3-letter IATA-shaped codes plus the region
  pseudo-codes documented in
  [docs/seats-aero-api.md](../../docs/seats-aero-api.md) § Region
  pseudo-codes — the same table
  [skills/getaway/SKILL.md](../getaway/SKILL.md#region-pseudo-codes)
  points at. Reject anything else. Storage splits by how the code is
  consumed: `home_airport` and `origin_airports` keep pseudo-codes
  verbatim — `getaway.sh search --origin` re-expands them server-side
  on every call — while `avoid_transit` expands a pseudo-code to its
  member airports at save, because transit enforcement matches literal
  segment IATA codes and a stored `WST` would never match an SFO
  connection.
- `avoid-transit` answers are comma-separated airport codes; split them
  into the `avoid_transit` array. A blank field keeps the current list,
  so omit the key; a literal `none` clears it, so send
  `"avoid_transit": []`.
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
- Document answers are comma-separated free text kept verbatim
  (`Canada`, `US green card`, `US B1/B2 to 2030`) — one array per
  field. A blank field keeps the current array; a literal `none` clears
  it to `[]`. The merge warning bites hardest here: the patch replaces
  the whole `documents` object and `prefs-set` rejects a partial one,
  so always send all three arrays, merged with the current values.

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
 "documents": {"passports": ["Canada"], "residency": ["US green card"], "visas": []},
 "op_ref": "op://Vault/item/field"}
JSON
```

```
/Users/<user>/.getaway/preferences.json
```

`prefs-status` flips to `configured` once a balance lands. Close by
running `prefs` and confirming the saved values with the user.
