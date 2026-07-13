---
name: onboard
description: Sets up getaway travel preferences. Triggers when the user wants to set up getaway ("set up getaway", "set up my travel preferences", "run getaway onboarding") or to record airports, airline and hotel points balances, elite statuses, status goals, travel instruments (airline eCredits, vouchers, companion certificates, hotel free-night certificates), travel documents (passports, residency, standing visas), layover preferences (minimize or explore, connection floor, long-stop cities), or avoid lists for award planning. Auto-fills from Gmail and logged-in airline and bank sites; nothing is written until the form's Submit. Refreshing balances already on file is /getaway:refresh.
allowed-tools: Bash(jq:*), Bash(op:*), Bash(gog:*), Bash(cookiesync:*), Bash(uv:*), Agent
---

# onboard

Onboarding collects the user's airports, balances — airline and hotel
programs alike — elite statuses, status goals, travel instruments,
travel documents — passports, residency, standing visas — and avoid
lists and writes them in one pass. It is
optional: the user may skip it and plan on the neutral template. The
CLI shorthand throughout:

```bash
CLI="uv run --project $CLAUDE_PLUGIN_ROOT/cli getaway"
```

When the user accepts onboarding, run auto-fill immediately — announce
each step, do not ask permission for it. Start at the main level with
Gmail query 1, the domain tally: the browser gatherer's host list
derives from it, and the mailbox question below is asked here, before
any spawn. Prime the cookie grant at the main level per gather.md's
[browser read](../refresh/gather.md#browser-read) — Touch ID denied
means the spawn goes Gmail-only, no browser gatherers. Then run the
gatherers below as parallel subagents in one spawn message — the
Gmail gatherer (reads 2–6, the calendar read among them, and body
fetches) beside one browser gatherer per host. The gatherers degrade
independently: skipping one costs nothing but its answers, and none of
them writes a byte. The form's Submit is the sole write gate, and the
form is never delegated.

## Auto-fill from Gmail

This section is the Gmail subagent's brief. Spawn it with the chosen
account and the tally-narrowed `from:` list; it returns
`{programs, statuses, balances, instruments, home_airport,
origin_candidates, document_signals, notes}` as JSON. Query 1 runs at
the main level first, so the tally can seed both gatherers.

Invocation mechanics — gog detection, the lockdown flags, and the
trust doctrine — live in gather.md's
[Gmail read](../refresh/gather.md#gmail-read-gog-lockdown) and
[Calendar read](../refresh/gather.md#calendar-read-gog-lockdown); Read
`${CLAUDE_PLUGIN_ROOT}/skills/refresh/gather.md` first.

Resolve the account per gather.md's account rule (`gog auth list
--json`; ask when more than one is configured, never guess). In
onboarding the mailbox pick happens at the main level before the
gatherers spawn — the lone pre-spawn question in this flow.

Run the six reads below — five headers-first Gmail queries and one
calendar read — fetching at most 10 message bodies total across
queries 1–5 per gather.md's body-fetch rule (the flight-history
fallback and the instruments-mining read carry their own budgets: 25 and
10):

1. **Programs, airlines, and banks** — `from:(<every domain from
   registry programs --domains and registry banks>) newer_than:1y`,
   `--max 100`, the domains from gather.md's
   [Program and bank registries](../refresh/gather.md#program-and-bank-registries).
   Tally sender domains with `jq`: the heavy hitters are the frequent
   airlines and the candidate programs, and bank senders with hits join
   the browser host list. This query is the main-level pre-spawn step;
   the remaining reads run inside the subagent.
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
   map to `balances.transferable` keys through `registry banks`.
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
6. **Travel instruments** — the
   [instruments-mining flow](../refresh/gather.md#instruments-mining-gmail)
   verbatim: headers-first over the registry domains, its own 10-body
   budget, returning `instruments` as tagged-union rows
   (`monetary_credit`, `hotel_night_certificate`, `companion_fare`)
   with issuer and program slugs from the registry. Rows reach the
   form's travel-instruments section as label suffixes — mined values
   are suggestions, adopted only by typing.

The flight-history fallback: `from:(<the program domains from
registry programs --domains>)
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
banks, any programs, hotels, or banks the user has named, and the
keys already in `balances.programs`, `statuses`, and
`balances.transferable` — banks and hotels need no special casing,
since each host gets its own gatherer and session and the shared
per-session grant keeps the whole fan-out at one tap — mapped to
login hosts and `gather_auth` classes through `$CLI registry hosts`
(gather.md's
[Program and bank registries](../refresh/gather.md#program-and-bank-registries)).
Route by class per gather.md's browser read: cookie hosts fan out in
parallel; token and device-wall hosts ride one sequential Arc-CDP
gatherer.
The mechanics — the priming `auth`, the per-host cookie pulls,
per-site extraction, the failure branches — are gather.md's
[Browser read](../refresh/gather.md#browser-read).

## Confirm in the form

Collect the answers with a cc-present form, not the approval board.
Seed each field's `placeholder` with the user's current preference,
from `$CLI prefs show` — on a fresh install `prefs show` exits 3
(uninitialized): run `$CLI prefs init` once and seed from the neutral
template it writes. The saved preference always wins the placeholder —
a discovery never displaces it. Auto-fill discoveries appear only as a
label suffix naming the source and its strength — `— Calendar suggests
YVR, weak: 2 of 3 segments`, `— united.com reads 88,000`, or `— Gmail:
delta eCredit $300, expires 2026-12-31` — never as the keep-on-blank
value; accepting one means typing it.
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
    { "id": "sec-layovers", "type": "section", "title": "Layovers" },
    { "id": "layovers-style", "type": "input", "label": "Layover style — minimize (shortest workable connections) or explore (happy to leave the airport on a long stop)", "placeholder": "minimize" },
    { "id": "layovers-min-connection", "type": "input", "label": "Shortest acceptable connection, in minutes", "placeholder": "75" },
    { "id": "layovers-prefer-cities", "type": "input", "label": "Cities worth a long layover — comma-separated IATA or seats.aero region codes; a region code expands to its airports on save", "placeholder": "none" },
    { "id": "layovers-avoid-cities", "type": "input", "label": "Cities never worth a long layover — comma-separated IATA or seats.aero region codes; a region code expands to its airports on save", "placeholder": "none" },
    { "id": "sec-balances", "type": "section", "title": "Mileage balances", "md": "List every program you hold. Format: program:points, comma-separated." },
    { "id": "balances-programs", "type": "input", "label": "Airline programs (program:points, comma-separated)", "placeholder": "aeroplan:88000, alaska:90000", "multiline": true },
    { "id": "balances-hotels", "type": "input", "label": "Hotel programs (program:points, comma-separated)", "placeholder": "hyatt:60000, marriott:120000", "multiline": true },
    { "id": "balances-transferable", "type": "input", "label": "Transferable points (bank:points, comma-separated)", "placeholder": "amex:150000, chase:80000", "multiline": true },
    { "id": "statuses", "type": "input", "label": "Elite status (program:tier, comma-separated)", "placeholder": "united:1K, hyatt:Globalist", "multiline": true },
    { "id": "sec-status-goals", "type": "section", "title": "Status goals" },
    { "id": "status-goals", "type": "input", "label": "Status targets (program:target:by, comma-separated — by is an ISO date)", "placeholder": "none", "multiline": true },
    { "id": "sec-instruments", "type": "section", "title": "Travel instruments", "md": "Instruments to add — the placeholder lists what's already on file. One per line, free text with the expiry date included: a monetary credit (delta eCredit $300 expires 2026-12-31), a hotel free-night certificate (hyatt free night, Category 1-4, expires 2027-01-31), or a companion fare (alaska companion fare expires 2027-06-30)." },
    { "id": "instruments-add", "type": "input", "label": "Instruments to add (one per line, expiry included)", "placeholder": "none", "multiline": true },
    { "id": "sec-documents", "type": "section", "title": "Travel documents" },
    { "id": "documents-passports", "type": "input", "label": "Passports held (countries, comma-separated)", "placeholder": "none" },
    { "id": "documents-residency", "type": "input", "label": "Residency and long-stay permits (comma-separated — US green card, UK ILR…)", "placeholder": "none" },
    { "id": "documents-visas", "type": "input", "label": "Standing visas (comma-separated — US B1/B2 to 2030…)", "placeholder": "none" },
    { "id": "sec-auth", "type": "section", "title": "seats.aero API key" },
    { "id": "op-ref", "type": "input", "label": "1Password reference for the seats.aero API key", "placeholder": "op://Vault/item/field" }
  ]
}
```

Drive it with `Skill(cc-present:present)` — the standard loop: push,
rounds, submit, outcomes, close.

Reading the outcomes back takes judgment; the form's free-text fields and
the preference schema differ:

- `input` blocks carry no seeded value — the placeholder displays what
  blank keeps. A field absent from the outcomes means the user left it
  blank: keep the placeholder's value — the current preference, or the
  neutral template on a fresh install — so omit it from the writes.
  A blank never adopts a discovery; adopting one takes a typed answer.
  Never overwrite a preference with an empty value.
- Airport answers accept 3-letter IATA-shaped codes plus the region
  pseudo-codes `$CLI registry regions` lists. Reject anything else.
  Storage splits by how the code is consumed: `home_airport` and
  `origin_airports` keep pseudo-codes verbatim — `search --origin`
  re-expands them server-side on every call — while `avoid_transit`
  expands a pseudo-code to its member airports at save, because
  transit enforcement matches literal segment IATA codes and a stored
  `WST` would never match an SFO connection.
- `avoid-transit` answers are comma-separated airport codes; split them
  into the `avoid_transit` array. A blank field keeps the current list,
  so omit the key; a literal `none` clears it, so send
  `"avoid_transit": []`.
- `avoid-airlines` answers are `name:soft|hard`, but the `avoid_airlines`
  preference stores `{code, name, strength}` objects matched on the IATA
  `code`. Resolve each airline name to its code yourself (Ethiopian is
  ET) and build the full object.
- Layover answers map to the `layovers` object. The style accepts only
  `minimize` or `explore` — reject anything else. The shortest
  connection parses to a positive integer of minutes. The two city
  lists split on commas, and a region pseudo-code expands to its member
  airports at save — the `avoid_transit` doctrine, because layover
  scoring matches literal segment IATA codes. A literal `none` clears a
  list to `[]`. The merge warning applies here too: the patch replaces
  the whole `layovers` object and `prefs set` rejects a partial one, so
  always send all four fields, merged with the current values — a blank
  field keeps its current subvalue, and a partially answered section
  still sends the full object.
- Balance answers are `program:points` free text — the airline, hotel,
  and transferable fields all parse the same way. Parse the points to
  integers; resolve names to registry slugs (Alaska is `alaska`,
  Hyatt is `hyatt`, Amex is `amex`). Each pair writes as one
  `$CLI prefs set-balance <slug> <points>` — the CLI validates the
  slug against the registry and routes it itself, airline and hotel
  programs to `balances.programs` and banks to
  `balances.transferable`. No merge dance: untouched balances stay
  put.
- Status answers are `program:tier` free text. Resolve program names to
  slugs the same way and keep the tier string verbatim (`1K`,
  `MVP Gold 75K`). Each pair writes as one
  `$CLI prefs set-status <slug> "<tier>"`.
- Status-goal answers are `program:target:by` free text — the program
  resolves to a registry slug, the target is a tier string kept
  verbatim, and `by` is an ISO date. Build
  `{program, target, by}` rows; the whole list rides the scalar patch
  as `status_goals`, and the patch replaces it whole, so send it
  merged with the current rows. A literal `none` clears it to `[]`.
- Instrument answers are free-text lines, one instrument each. Build
  the tagged-union object per line — `monetary_credit`
  (`{type, issuer, amount, currency, expires}`),
  `hotel_night_certificate` (`{type, program, nights, cap, expires}`,
  the program a hotel slug and `cap` one of
  `{type: "points", points}` / `{type: "category", category}` /
  `{type: "anytime"}`), or `companion_fare` (`{type, issuer,
  expires}`, with no amount field at all) — and write each as one
  `$CLI prefs instrument-add` with the JSON object on stdin. The CLI
  generates the `id`, requires the expiry, and rejects unknown types,
  missing or extra keys, and non-hotel certificate programs — a mined
  row missing its expiry included, so the date has to come from the
  user. This field only adds: blank adds nothing, and the on-file
  list keeps every record — removal is
  `$CLI prefs instrument-remove <id>`, run only when the user asks.
- Document answers are comma-separated free text kept verbatim
  (`Canada`, `US green card`, `US B1/B2 to 2030`) — one array per
  field. A blank field keeps the current array; a literal `none` clears
  it to `[]`. The merge warning bites hardest here: the patch replaces
  the whole `documents` object and `prefs set` rejects a partial one,
  so always send all three arrays, merged with the current values.

Write per-record values first, then one scalar patch. Balances,
statuses, and credits go through their first-class commands — one call
per record, shown above. Everything else — `home_airport`,
`origin_airports`, the avoid lists, `layovers`, `status_goals`,
`documents`, `op_ref` — goes in ONE `$CLI prefs set` patch on stdin.
The merge is top-level: each key in the patch replaces that whole key,
every omitted key keeps its current value, and `prefs set` rejects
unknown keys. A blank form field is omitted from the patch and from
the per-record calls — never overwrite a preference with an empty
value. A real write:

```bash
CLI="uv run --project $CLAUDE_PLUGIN_ROOT/cli getaway"
$CLI prefs set-balance aeroplan 88000
$CLI prefs set-balance hyatt 60000
$CLI prefs set-balance amex 150000
$CLI prefs set-status united 1K
$CLI prefs instrument-add <<'JSON'
{"type": "monetary_credit", "issuer": "delta", "amount": 300,
 "currency": "USD", "expires": "2026-12-31",
 "note": "eCredit from cancelled LAX"}
JSON
$CLI prefs set <<'JSON'
{"home_airport": "SFO",
 "avoid_airlines": [{"code": "ET", "name": "Ethiopian Airlines", "strength": "soft"}],
 "status_goals": [{"program": "united", "target": "1K", "by": "2027-01-31"}],
 "documents": {"passports": ["Canada"], "residency": ["US green card"], "visas": []},
 "op_ref": "op://Vault/item/field"}
JSON
```

```
/Users/<user>/.getaway/preferences.json
```

`prefs status` exits 0 once a balance lands; before that it exits 1,
and the hooks gate on exactly that. Close by running `$CLI prefs show`
and confirming the saved values with the user.
