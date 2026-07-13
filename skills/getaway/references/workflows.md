# Workflows

The contract is the CLI; the script is a template. `plan-trip.js` is the codified reference walker for the canonical ask, and any ad-hoc workflow that follows the rules below is an equal citizen. The compiled graph is the walker's entire world-model: never re-derive phase lists, dependency tables, or guard sets from prose.

## Consume the graph

- `trip compile <slug>` emits the execution graph: `{slug, trip_type, lodging, requires, quota_budget, nodes}`. Each node carries `id`, `kind`, `inputs`/`outputs` (artifact names, the dependency edges), `routing` (`{model, effort}`), `requires`, `quota_cost`, `ttl_hours`, and either a ready-made `command` argv or `command: null` for an agent-shaped node (`assess`, `stays`). The `stays` node also carries `steps` (`intervals`, `ingest`) to splice.
- `trip explain <slug>` is compile plus a `fresh` flag per node; `trip status <slug>` returns the same freshness as `phase_map`. Only compiled node ids exist: `trip phase-done` rejects anything else.
- Order execution by the artifact edges: a node is ready once every input with a producer in the graph has run. Skip nodes the phase map calls `fresh` (unless refreshing), and re-read `trip status` after each round — an upstream re-run stales downstream nodes the initial snapshot called fresh.

## Run emitted commands verbatim

Splice a runnable node's whole `command` argv behind `uv run --project <cli>`, unchanged. The only additions are policy flags: `--quota-floor N` on quota-costed nodes (`quota_cost > 0`), `--refresh` on sweeps when the caller forces one. Never assemble a getaway command from prose, and guard any value an agent returned before it lands in a prompt as part of a command line — the graph itself arrives through an agent on the Workflow host, so even its tokens count.

## Node state is CLI truth

Runnable commands stamp their own checkpoints; `assess` stamps via `trip phase-done <slug> assess` after writing its artifact; `stays` stamps inside `stays ingest`. The walker trusts none of it on prose: after every dispatch round it re-reads `trip status`, retries any unstamped node exactly once, then records that node as `failed`. Fan-out results are possibly-null (`pipeline`/`parallel` resolve a failed agent to null) — filter, feed nulls the same retry-then-failed path, never dereference. Schema-force every result that carries data, and treat a result that fails its shape as a null.

## Preflight `requires`

Compile is pure: it emits `requires` (for example `rooms_session` when lodging is in scope) and never inspects session state. The walker verifies each requirement before any dispatch and fails loudly when one is missing. For `rooms_session`: open rooms.aero in the seeded `agent-browser --session rooms` session and confirm the nav shows the account email, a PRO badge, and Logout — anything else is not a logged-in Pro session. A requirement the walker has no preflight for is a shape surprise (see below).

## Quota policy

Quota is enforced at the HTTP boundary (`--quota-floor`, default 100; `0` is a deliberate spend-down). Exit 1 from a quota-costed node is a quota stop, distinct from data failure: the CLI wrote a partial artifact and deliberately left the node unstamped so a later run resumes cache-first. Record it as `not_run {quota_floor}`, never retry it, and keep walking — downstream nodes run over the partial and the board surfaces the honest `not_run` states. Sweeps fold a floor stop into their artifact's `search_states` and still stamp; `expand run` is the one that exits 1.

## Routing

Every agent runs at its node's emitted `routing`. The pins also shrink the blast radius when one account limit kills a fan-out, since cheap lanes don't share the orchestrator's limit.

| Lane | Work | Route |
|---|---|---|
| Mechanical runner | Emitted-command execution, JSON shaping, status re-reads | `sonnet` low |
| Single-fact labeling | Classify or label one thing per item | `haiku` |
| Research and judgment | Evidence collectors, `assess`, the stays walk | `opus` xhigh, or gpt-5.6-terra via the `codex:codex-wrapper` agent type (Workflow `model` opts take only Claude models) |

Fable never runs a trip-planning subagent; a walker that ignores routing burns fable-class tokens on sweep runners.

## Return early with options

A trip shape the walker can't express — an agent-shaped node kind it has no handler for, a `requires` it can't preflight — stops the walk and returns findings plus two to four concrete options for the orchestrator. Never improvise a detour.

## Testing a conforming script

`tests/workflow/harness.mjs` re-parses the real script and stubs `agent()` by `opts.label`, enforcing each schema's required keys on object payloads. `runWorkflow(args, script, path)` takes any conforming script's absolute path, so an ad-hoc walker tests against the same harness as `plan-trip.js`. Workflow args arrive as an object or a JSON string — adapt once at entry, parse fail-loud, and keep everything downstream object-only.
