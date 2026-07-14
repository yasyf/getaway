---
name: refresh
description: Refreshes getaway points, miles, elite statuses, and travel instruments. Triggers when the user wants to refresh or update balances ("refresh my balances", "update my Aeroplan balance", "how many points do I have now"), re-check elite status, pull current credit-card points — Amex Membership Rewards, Chase Ultimate Rewards, Citi ThankYou, Capital One miles — refresh hotel points (Hyatt, Hilton, Marriott, IHG, Choice, Wyndham), or re-check travel instruments ("refresh my credits", "what credits are expiring", companion certificates, hotel free nights), or pull balances from AwardWallet ("refresh from AwardWallet", "pull my AwardWallet balances"). Reads AwardWallet first when a key is on file, then logged-in airline, hotel, and bank sites, with a Gmail statement fallback for banks and a Gmail mining pass for instruments. First-time setup is /getaway:onboard.
allowed-tools: Bash(jq:*), Bash(op:*), Bash(gog:*), Bash(cookiesync:*), Bash(uv:*), Agent
---

# refresh

Refreshes the balances, statuses, and travel instruments already on
file, outside onboarding. Read `${CLAUDE_PLUGIN_ROOT}/skills/refresh/gather.md`
first — it holds the shared mechanism: the registry commands, the
AwardWallet read, the browser read and its `gather_auth` routing, the
Gmail read, and the instruments-mining flow. The CLI shorthand throughout:

```bash
CLI="uv run --project $CLAUDE_PLUGIN_ROOT/cli getaway"
```

0. Gate on configuration: `$CLI prefs status` exits 0 when configured,
   1 when not. On 1 there is nothing to refresh; offer
   `/getaway:onboard` and stop.
1. Read `$CLI prefs show` and derive the host list: airline and hotel
   hosts from the current `balances.programs` and `statuses` keys,
   bank hosts from the `balances.transferable` keys — plus any program
   or bank the user names — joined against `$CLI registry hosts`,
   which carries each host's `gather_auth` class
   ([Program and bank registries](gather.md#program-and-bank-registries)).
2. Run the [AwardWallet read](gather.md#awardwallet-read): one
   `$CLI awardwallet pull`, rows adopted through its gate. Adopted
   rows join the record set tagged `awardwallet`, each with its
   `age_days`, and their slugs leave the step-1 host list before the
   priming tap. Exit 2, exit 4, or an empty adopted set: the browser
   read runs over the full step-1 list unchanged.
3. Run the [browser read](gather.md#browser-read): prime the grant at
   the main level — one `cookiesync auth` tap, its `--reason` naming
   every host — then route by `gather_auth`: one gatherer per
   cookie-class host in parallel, one sequential Arc-CDP gatherer for
   the token and device-wall hosts, per gather.md. Aggregate the
   per-gatherer records into `[{slug, balance, tier}]` and reconcile
   the per-host ledger — every derived host ends the run as read,
   retried, or skipped with its reason.
4. Per bank host not adopted in step 2 that skipped `logged-out` or
   `2fa`, run the
   [Gmail read](gather.md#gmail-read-gog-lockdown) with the bank-points
   query narrowed to that sender domain; `no-cookies` and `hung` skips
   join the main-level retry offer instead. Browser-read numbers
   override Gmail hints; failed airline hosts stay note-and-skip.
5. Run the [instruments-mining flow](gather.md#instruments-mining-gmail)
   and match its rows against `$CLI prefs instrument-list` by issuer
   or program plus type. Update only instruments a gathered row
   matches: `instrument-remove` the stale record, then
   `instrument-add` the gathered values (one JSON object on stdin),
   keeping the on-file `expires` when the mined row carries none. A
   gathered row with no on-file match is a discovery — report it, and
   add it only when the user says so. An on-file instrument past its
   `expires` is reported as expired, never silently dropped; removing
   it takes an explicit `$CLI prefs instrument-remove <id>`.
6. Write at the main level, per record, no form round-trip — the main
   level is the one writer; a gatherer never writes.
   `$CLI prefs set-balance <slug> <amount>` per program — airline and
   hotel alike — and per bank, `$CLI prefs set-status <slug> "<tier>"`
   per tier change, and the `instrument-remove`/`instrument-add` pairs
   from step 5. The explicit request plus the Touch ID tap are the
   consent. `set-balance` and `set-status` reject slugs outside the
   registry, so resolve names first.
7. Report the deltas, old value to new: per program, per bank, and per
   instrument, each delta naming its source — `awardwallet (as of N
   days ago)`, `browser`, or `gmail hint` — with an `error_code` 9
   `error_message` beside its delta. Mark Gmail-derived numbers as
   stale statement hints, call out expired instruments, and flag
   everything `$CLI prefs instrument-list --expiring-within 90d`
   returns — plus any AwardWallet `expiration` or `status_expiration`
   landing soon, reported beside them but never written.
