---
name: refresh
description: Refreshes getaway points, miles, and trip credits. Triggers when the user wants to refresh or update balances ("refresh my balances", "update my Aeroplan balance", "how many points do I have now"), re-check elite status, pull current credit-card points — Amex Membership Rewards, Chase Ultimate Rewards, Citi ThankYou, Capital One miles — or re-check trip credits and vouchers ("refresh my credits", "what credits are expiring"). Reads logged-in airline and bank sites first, with a Gmail statement fallback for banks and a Gmail mining pass for credits. First-time setup is /getaway:onboard.
allowed-tools: Bash(jq:*), Bash(op:*), Bash(gog:*), Bash(cookiesync:*), Bash(uv:*), Agent
---

# refresh

Refreshes the balances, statuses, and trip credits already on file,
outside onboarding. Read `${CLAUDE_PLUGIN_ROOT}/skills/refresh/gather.md`
first — it holds the shared mechanism: the registry commands, the
browser read, the Gmail read, and the credits-mining flow. The CLI
shorthand throughout:

```bash
CLI="uv run --project $CLAUDE_PLUGIN_ROOT/cli getaway"
```

0. Gate on configuration: `$CLI prefs status` exits 0 when configured,
   1 when not. On 1 there is nothing to refresh; offer
   `/getaway:onboard` and stop.
1. Read `$CLI prefs show` and derive the host list: airline hosts from
   the current `balances.programs` and `statuses` keys, bank hosts from
   the `balances.transferable` keys — plus any program or bank the user
   names — joined against `$CLI registry programs --domains` and
   `$CLI registry banks`
   ([Program and bank registries](gather.md#program-and-bank-registries)).
2. Run the [browser read](gather.md#browser-read): prime the grant at
   the main level — one `cookiesync auth` tap, its `--reason` naming
   every host — then spawn one gatherer per host in parallel per
   gather.md. Aggregate the per-gatherer records into
   `[{slug, balance, tier}]`.
3. Per bank host that skipped `logged-out` or `2fa`, run the
   [Gmail read](gather.md#gmail-read-gog-lockdown) with the bank-points
   query narrowed to that sender domain; `no-cookies` and `hung` skips
   join the main-level retry offer instead. Browser-read numbers
   override Gmail hints; failed airline hosts stay note-and-skip.
4. Run the [credits-mining flow](gather.md#credits-mining-gmail) and
   match its rows against `$CLI prefs credit-list` by issuer. Update
   only credits whose issuer matches a gathered row: `credit-remove`
   the stale record, then `credit-add` the gathered values, keeping the
   on-file `expires` when the mined row carries none. A gathered row
   with no on-file match is a discovery — report it, and add it only
   when the user says so. An on-file credit past its `expires` is
   reported as expired, never silently dropped; removing it takes an
   explicit `$CLI prefs credit-remove <id>`.
5. Write at the main level, per record, no form round-trip — the main
   level is the one writer; a gatherer never writes.
   `$CLI prefs set-balance <slug> <amount>` per
   program and bank, `$CLI prefs set-status <slug> "<tier>"` per tier
   change, and the `credit-remove`/`credit-add` pairs from step 4. The
   explicit request plus the Touch ID tap are the consent.
   `set-balance` and `set-status` reject slugs outside the registry,
   so resolve names first.
6. Report the deltas, old value to new: per program, per bank, and per
   credit. Mark Gmail-derived numbers as stale statement hints, call
   out expired credits, and flag everything
   `$CLI prefs credit-list --expiring-within 90d` returns.
