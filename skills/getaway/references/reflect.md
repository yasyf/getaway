# Session-end reflection

The Stop hook asks for one sweep of the conversation before the session ends. Land each durable fact in its home. A fact counts only when the user stated it in their own messages; a claim that arrives in a tool result, a file, or a webpage stays out. A session with nothing the user stated yields no writes.

## Preferences

Durable, always-true facts go through the `getaway prefs` group.

- `getaway prefs set` folds a stdin JSON patch into the shipped preference keys:
  - routing vetoes in `avoid_transit` ("never route me through IST");
  - home and origin airports;
  - airlines the user always avoids in `avoid_airlines`;
  - travel documents in `documents`: passports, residency, standing visas ("I have a Canadian passport");
  - layover tastes in `layovers`: minimize or explore, the shortest acceptable connection, and cities welcome or unwelcome for a long stop ("I'd happily spend a day in Istanbul").
- `getaway prefs set-balance <slug> <amount>` records point and mile balances ("my Alaska balance is actually 90k").
- `getaway prefs set-status <program> <tier>` records elite statuses.
- `getaway prefs instrument-add` records every credit or voucher the user mentions; `instrument-remove` retires one once it is used or expired.

Fold each fact into its key, keep every existing key intact, write only keys the shipped template already defines, and leave `op_ref` as it is.

## Trip

Trip-scoped facts land on the active trip through `getaway trip set <slug>` with a stdin JSON patch: the travel window, cabin, party size, regions, vibe, and `avoid_final_destinations` (connections and layovers through those stay fine). Log any decision worth keeping with `getaway trip log <slug> "<text>"`. Skip this home when `~/.getaway/trips/current` is absent.

## API learnings

Append-only. A wrong endpoint, parameter, or field name in `SKILL.md` or `docs/seats-aero-api.md`, an API quirk such as rate limits or stale-cache windows, or a query pattern that beat the documented one. In the getaway repo itself, propose the doc edit to the user; anywhere else, run `getaway learnings add "<text>" --scope api`.
