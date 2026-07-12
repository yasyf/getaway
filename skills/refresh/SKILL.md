---
name: refresh
description: Refreshes getaway points and miles balances. Triggers when the user wants to refresh or update balances ("refresh my balances", "update my Aeroplan balance", "how many points do I have now"), re-check elite status, or pull current credit-card points — Amex Membership Rewards, Chase Ultimate Rewards, Citi ThankYou, Capital One miles. Reads logged-in airline and bank sites first, with a Gmail statement fallback for banks. First-time setup is /getaway:onboard.
allowed-tools: Bash(jq:*), Bash(op:*), Bash(gog:*), Agent
---

# refresh

Refreshes the balances and statuses already on file, outside
onboarding. Read `${CLAUDE_PLUGIN_ROOT}/skills/refresh/gather.md`
first — it holds the shared mechanism: the domain tables, the browser
read, and the Gmail read.

0. Check configuration:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/skills/getaway/getaway.sh" prefs-status
   ```

   On `unconfigured` there is nothing to refresh; offer
   `/getaway:onboard` and stop.
1. Read `prefs` and derive the host list: airline hosts from the
   current `balances.programs` and `statuses` keys, bank hosts from the
   `balances.transferable` keys — plus any program or bank the user
   names — mapped through the tables in
   [Program and bank domains](gather.md#program-and-bank-domains).
2. Run the [browser read](gather.md#browser-read) over all hosts as one
   subagent — one cookie pull, one Touch ID tap, the `--reason` naming
   every host; it returns `[{slug, balance, tier}]`.
3. Per failed bank host, run the
   [Gmail read](gather.md#gmail-read-gog-lockdown) with the bank-points
   query narrowed to that sender domain. Browser-read numbers override
   Gmail hints; failed airline hosts stay note-and-skip.
4. Merge the results into the current `balances.programs`,
   `balances.transferable`, and `statuses` maps and write with
   `prefs-set` at the main level
   ([one writer](../getaway/SKILL.md#orchestration)) — no form
   round-trip. The explicit request plus the Touch ID tap are the
   consent. The top-level merge replaces each map whole, so every patch
   sends fully merged maps; `statuses` may be absent from a real prefs
   file — treat it as empty. `prefs-set` rejects unknown keys, so
   patches carry only current template keys.
5. Report the per-program and per-bank deltas, old value to new; mark
   Gmail-derived numbers as stale statement hints.
