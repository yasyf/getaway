# AwardWallet Account Access API reference

Working notes for building against the AwardWallet Account Access API,
distilled from the [official docs](https://awardwallet.com/api/account) as
of July 2026 and checked against live captures on 2026-07-14. The API is
raw REST returning JSON; there is no official Python client.

## Access and auth

The Account Access API is free, but it hangs off an AwardWallet business
account whose admin holds AwardWallet Plus. Generate the key on the
business console at business.awardwallet.com/profile/api.

Users connect to the business account through one of two paths. getaway
uses the manual invite: the business console sends an invite, the member
accepts, and their accounts appear under `/connectedUser`. The programmatic
`POST /create-auth-url` OAuth-style flow exists but is approval-gated and
unused here.

Keys go in the `X-Authentication` header, not `Authorization`, on every
request. The base URL is
`https://business.awardwallet.com/api/export/v1` — the version lives in
the path, and there is no version header.

getaway resolves the key through the shared `keys.resolve` order: the
`AWARDWALLET_API_KEY` env var wins, else the `awardwallet_op_ref`
preferences key, a 1Password `op://` reference read via `op read`.

No rate limits are documented. getaway's client
(`cli/getaway/awardwallet.py`) walks users and accounts sequentially with
no retries.

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/connectedUser` | GET | List every user connected to the business account |
| `/connectedUser/{id}` | GET | One connected user's detail, including its `accounts` array |
| `/account/{id}` | GET | Full detail for one loyalty account |
| `/providers/list` | GET | Every loyalty provider AwardWallet can track |

Envelopes are inconsistent across the four; each subsection records the
observed shape.

### List connected users (`GET /connectedUser`)

The response is an envelope, not a bare array:
`{"connectedUsers": [...]}` (verified live 2026-07-14 — the docs' shape
reads ambiguously). Each user carries `userId` (a number), `fullName`,
`userName`, `email`, `status`, `connectionType`, `accessLevel`,
`accountsAccessLevel`, `accountsSharedByDefault`, and an `accountsIndex`
array.

`accountsIndex` exists so a poller can skip unchanged accounts. getaway's
v1 client deliberately ignores it: there is no persisted per-account state
to diff against, so every pull walks every shared account.

### One connected user (`GET /connectedUser/{id}`)

The response is a flat user object with an `accounts` array — no envelope
(verified live 2026-07-14).

### One account (`GET /account/{id}`)

The response is an `{"account": {...}}` envelope per the docs. Not yet
live-verified: zero accounts were shared with the business account at
verification time.

### Providers (`GET /providers/list`)

The full provider list — 256 providers live on 2026-07-14. Diff it against
the registry mapping before trusting a new key's pulls; see the
verification checklist below.

## Data shapes

An account object carries `code` (the provider code), `accountId`,
`displayName`, `kind`, `owner`, the two balance fields, `expirationDate`,
`lastRetrieveDate`, `lastChangeDate`, `errorCode`, an optional
`errorMessage`, a `properties` array, and `subAccounts` plus `history`.
`normalize_account` in `cli/getaway/awardwallet.py` records which fields
getaway projects onto its balance-row shape.

Type quirks to handle:

- Two balance fields: `balance` is a display string (`"88,450"`) while
  `balanceRaw` is a number. getaway reads `balanceRaw` only; the display
  string is never parsed.
- `properties[]` entries are keyed by an integer `kind`: kind 3 is the
  elite tier (a display `value` plus a standardized numeric `rank`), and
  kind 15 is the elite-status expiration date.
- The top-level `expirationDate` is the miles/points expiry — distinct
  from the kind-15 status expiration.
- `owner` names the account holder. With AwardWallet family sharing that
  may not be the connected user.
- `subAccounts` and `history` exist on the account object; getaway v1
  deliberately ignores both.
- `errorMessage` is optional; it carries the warning text for error
  code 9.

### Error codes

A nonzero `errorCode` is data, not an exception. The full table (verified
2026-07-14 against the official docs):

| Code | Meaning |
|---|---|
| 0 | Never updated |
| 1 | Successfully updated |
| 2 | Invalid credentials |
| 3 | Locked out |
| 4 | Provider error or user action required |
| 5 | Provider disabled by AwardWallet |
| 6 | Parse failure |
| 7 | Password missing |
| 8 | Disabled to prevent lockout |
| 9 | Updated with a warning (`errorMessage` carries it) |
| 10 | Security question required |
| 11 | Timeout |

getaway's refresh adopts balances only from codes 1 and 9.

### Timestamps

The docs' example timestamps carry no UTC offset (verified 2026-07-14 that
this is the documented example format). getaway treats offset-less values
as UTC.

## Provider mapping (registry)

Every row in `cli/getaway/data/programs.json` and `banks.json` carries an
`awardwallet` field — the AwardWallet provider code, or `null` —
surfaced through `registry.awardwallet_map()`. 24 of the 38 registry
entries map (diffed against the live provider list 2026-07-14):

| getaway slug | AwardWallet code |
|---|---|
| `aeroplan` | `aeroplan` |
| `alaska` | `alaskaair` |
| `lufthansa` | `lufthansa` |
| `singapore` | `singaporeair` |
| `qatar` | `qmiles` |
| `turkish` | `turkish` |
| `emirates` | `skywards` |
| `qantas` | `qantas` |
| `velocity` | `velocity` |
| `virginatlantic` | `virgin` |
| `jetblue` | `jetblue` |
| `aeromexico` | `aeromexico` |
| `connectmiles` | `copaair` |
| `azul` | `azul` |
| `smiles` | `golair` |
| `saudia` | `saudisrabianairlin` (sic) |
| `iberia` | `iberia` |
| `hyatt` | `goldpassport` |
| `hilton` | `hhonors` |
| `marriott` | `marriott` |
| `ihg` | `ichotelsgroup` |
| `amex` | `amex` |
| `chase` | `chase` |
| `citi` | `citybank` (sic) |

Codes are not guessable from names — `citybank`, `goldpassport`, and
`saudisrabianairlin` are real. The data files are the source of truth;
this table is the 2026-07-14 snapshot.

13 entries are aggregator-blocked, meaning the provider blocks
AwardWallet, the field stays `null` forever, and balances ride the
browser lane instead: American, Delta, United, British Airways, Flying
Blue, SAS EuroBonus, Etihad, Ethiopian, Finnair, Frontier, Choice,
Wyndham, and Capital One.
The one remaining `null` is Spirit, defunct since 2026-05-02, whose slug
survives only to mirror the seats.aero source list.

## Live-verification checklist

The once-per-key ritual before trusting refresh writes. Status as of
2026-07-14:

1. Diff `/providers/list` against `registry.awardwallet_map()` before the
   first refresh write — done 2026-07-14.
2. Validate pull shapes against live responses — done 2026-07-14; this
   caught the `/connectedUser` envelope.
3. Share accounts with the business account (`accountsIndex` was empty at
   verification), then re-pull to verify the `/account/{id}` envelope and
   the balance rows — remaining.
4. Run a full `/getaway:refresh` — remaining.

## Sources consulted

- https://awardwallet.com/api/account
- https://awardwallet.com/api/main
- Live API capture, 2026-07-14
