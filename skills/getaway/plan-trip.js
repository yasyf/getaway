export const meta = {
  name: 'plan-trip',
  description:
    'Reference graph walker over the getaway CLI: trip compile/explain emit the execution graph — dependency inputs, ready-made command lines, per-node model routing, requires — and the walker dispatches it level by level. Runner nodes execute their emitted command verbatim at their emitted routing; judgment nodes (evidence, assess, the rooms.aero stays walk) stay agent-shaped on the research lane. Checkpoint state read back from the CLI is the only truth about completion: an unstamped node retries once, then surfaces as failed. Resumable — fresh nodes skip wholesale, and a quota-floor stop resumes cache-first on the next run.',
  phases: [
    { title: 'Load', detail: 'One agent reads trip explain — the compiled graph is the walker world-model — plus the judged factors from trip show.' },
    { title: 'Preflight', detail: 'Every graph-level requirement gets its cheapest honest check before any dispatch; rooms_session verifies the seeded Pro session.' },
    { title: 'Walk', detail: 'Graph levels dispatch in dependency order: emitted commands run verbatim at emitted routing, the CLI stamps its own checkpoints, and the walker re-reads them after every round — one retry, then failed.' },
    { title: 'Evidence', detail: 'Zero-quota research collectors for the judged factors that own one, each persisting its own artifact for assess to read.' },
    { title: 'Assess', detail: 'One research agent weighs whole journeys into per-leg verdicts plus notable stretches, writes assess.json, and stamps its node.' },
    { title: 'Stays', detail: 'One browser agent walks rooms.aero over the stays intervals worklist and pipes one document to stays ingest, which validates, writes, and stamps.' },
  ],
};

// The harness sometimes hands args JSON-stringified (observed boundary contract); reparse fail-loud.
if (typeof args === 'string') {
  try {
    args = JSON.parse(args);
  } catch {
    throw new Error(`plan-trip: args arrived as a non-JSON string: ${args}`);
  }
}

// Guards for every string spliced into a prompt command line; the graph arrives through the Load
// agent, so even its command tokens are tainted at this boundary.
const ABS_PATH = /^\/[^\s"`$;|&\n<>()\\]*$/;
const SLUG = /^[a-z0-9][a-z0-9-]{1,63}$/;
const CMD_TOKEN = /^[A-Za-z0-9:._-]+$/;
// Sweep spec is <leg-id>[:<label>]; bare <leg-id> for per-leg commands. Label max = source(≤32) +
// "-from-" + slugged continent(≤13) = 51 (sweeps.derive_specs / trips._leg_sweep_labels).
const LEG_ID = /^[a-z0-9][a-z0-9-]{0,31}$/;
const LEG_SPEC = /^[a-z0-9][a-z0-9-]{0,31}(:[a-z0-9][a-z0-9-]{0,50})?$/;
const IATA = /^[A-Z]{3}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const JOURNEY_ID = /^[A-Za-z0-9:|._-]+$/;
const INPUTS_FP = /^[0-9a-f]{64}$/;

// Judged factor -> zero-quota Evidence collector (mirrors cli/getaway/constants.py EVIDENCE_COLLECTORS).
const COLLECTOR_OF = {
  seat_quality: 'verify',
  cash_anomaly: 'cash',
  destination_context: 'context',
  transit_risk: 'transit',
};

const NODE_SCHEMA = {
  type: 'object',
  required: ['id', 'kind', 'inputs', 'outputs', 'routing', 'requires', 'command', 'steps', 'quota_cost', 'fresh'],
  properties: {
    id: { type: 'string' },
    kind: { type: 'string' },
    inputs: { type: 'array', items: { type: 'string' } },
    outputs: { type: 'array', items: { type: 'string' } },
    routing: { type: 'object', required: ['model', 'effort'], properties: { model: { type: 'string' }, effort: { type: 'string' } } },
    requires: { type: 'array', items: { type: 'string' } },
    command: { type: ['array', 'null'], items: { type: 'string' } },
    steps: { type: 'array', items: { type: 'object', required: ['name', 'command'] } },
    quota_cost: { type: 'number' },
    fresh: { type: 'boolean' },
  },
};
const LOAD_SCHEMA = {
  type: 'object',
  required: ['graph', 'judgmentFactors'],
  properties: {
    graph: {
      type: 'object',
      required: ['slug', 'trip_type', 'lodging', 'requires', 'nodes'],
      properties: {
        slug: { type: 'string' },
        trip_type: { type: 'string' },
        lodging: { type: 'boolean' },
        requires: { type: 'array', items: { type: 'string' } },
        nodes: { type: 'array', items: NODE_SCHEMA },
      },
    },
    judgmentFactors: { type: 'array', items: { type: 'string' } },
  },
};
const STATUS_SCHEMA = { type: 'object', required: ['phaseMap'], properties: { phaseMap: { type: 'object' } } };
const PREFLIGHT_SCHEMA = {
  type: 'object',
  required: ['loggedIn', 'pro'],
  properties: { loggedIn: { type: 'boolean' }, pro: { type: 'boolean' } },
};
const RUN_SCHEMA = { type: 'object', required: ['exit_code'], properties: { exit_code: { type: 'number' }, stderr_tail: { type: 'string' } } };
const FINALIZE_SCHEMA = {
  type: 'object',
  required: ['exit_code', 'journeys', 'unpaired_leads', 'notable_stretches'],
  properties: {
    exit_code: { type: 'number' },
    journeys: { type: 'number' },
    unpaired_leads: { type: 'number' },
    notable_stretches: { type: 'number' },
    stderr_tail: { type: 'string' },
  },
};
const EVIDENCE_SCHEMA = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, count: { type: 'number' } } };
const ASSESS_SCHEMA = {
  type: 'object',
  required: ['ok'],
  properties: { ok: { type: 'boolean' }, journeys: { type: 'number' }, notable_stretches: { type: 'number' } },
};
const SCOUT_SCHEMA = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, airports: { type: 'number' } } };
const INTERVALS_SCHEMA = {
  type: 'object',
  required: ['inputs_fp', 'journeys'],
  properties: { inputs_fp: { type: 'string' }, journeys: { type: 'array' } },
};
const STAYS_WALK_SCHEMA = {
  type: 'object',
  required: ['ok'],
  properties: { ok: { type: 'boolean' }, walked: { type: 'number' }, ingested: { type: 'number' }, states: { type: 'object' } },
};

// Validate the args off the wire; everything else comes from the compiled graph via the CLI.
if (typeof args.project !== 'string' || !ABS_PATH.test(args.project)) throw new Error('plan-trip: project must be an absolute path to the cli/ dir');
if (typeof args.slug !== 'string' || !SLUG.test(args.slug)) throw new Error('plan-trip: slug must match ^[a-z0-9][a-z0-9-]{1,63}$');
const refresh = args.refresh === undefined ? false : args.refresh;
if (typeof refresh !== 'boolean') throw new Error('plan-trip: refresh must be a boolean');
const quotaFloor = args.quotaFloor === undefined ? 100 : args.quotaFloor;
if (!Number.isInteger(quotaFloor)) throw new Error('plan-trip: quotaFloor must be an integer');
const researchLane = args.researchLane === undefined ? 'opus' : args.researchLane;
if (researchLane !== 'opus' && researchLane !== 'terra') throw new Error('plan-trip: researchLane must be "opus" or "terra"');
const exit3BackoffMs = args.exit3BackoffMs === undefined ? 60000 : args.exit3BackoffMs;
if (!Number.isInteger(exit3BackoffMs) || exit3BackoffMs < 0) throw new Error('plan-trip: exit3BackoffMs must be a non-negative integer');
// Clock boundary is injectable so tests observe the exit-3 backoff without real waiting.
const sleep = args.sleep === undefined ? (ms) => new Promise((resolve) => setTimeout(resolve, ms)) : args.sleep;
if (typeof sleep !== 'function') throw new Error('plan-trip: sleep must be a function');

const project = args.project;
const slug = args.slug;
const CLI = `uv run --project ${project} getaway`;

const gLegId = (v) => { if (typeof v !== 'string' || !LEG_ID.test(v)) throw new Error(`plan-trip: unsafe leg id ${v}`); return v; };
const gIata = (v) => { if (typeof v !== 'string' || !IATA.test(v)) throw new Error(`plan-trip: unsafe airport code ${v}`); return v; };
const gDate = (v) => { if (typeof v !== 'string' || !DATE.test(v)) throw new Error(`plan-trip: unsafe date ${v}`); return v; };
const gJourneyId = (v) => { if (typeof v !== 'string' || !JOURNEY_ID.test(v)) throw new Error(`plan-trip: unsafe journey id ${v}`); return v; };
const gFp = (v) => { if (typeof v !== 'string' || !INPUTS_FP.test(v)) throw new Error(`plan-trip: unsafe inputs fingerprint ${v}`); return v; };
const gNights = (v) => { if (!Number.isInteger(v) || v < 1 || v > 5) throw new Error(`plan-trip: unsafe nights ${v}`); return v; };
const gArgv = (argv) => {
  if (!Array.isArray(argv) || argv.length < 2 || argv[0] !== 'getaway') throw new Error(`plan-trip: unexpected command shape ${JSON.stringify(argv)}`);
  for (const tok of argv) if (typeof tok !== 'string' || !CMD_TOKEN.test(tok)) throw new Error(`plan-trip: unsafe command token ${tok}`);
  return argv;
};

// The exact argv shapes trip compile emits per node kind (cli/getaway/trips.py compile_graph),
// slug positions pinned to this walker's own slug.
const KIND_COMMANDS = {
  sweep: [['sweep', 'run', slug, LEG_SPEC]],
  shortlist: [['shortlist', 'run', slug, '--leg', LEG_ID]],
  onward: [['shortlist', 'onward', slug, '--leg', LEG_ID]],
  bridge: [['bridge', slug, '--leg', LEG_ID]],
  expand: [['expand', 'run', slug]],
  rank: [['rank', slug]],
  finalize: [['trip', 'finalize', slug]],
};
const STEP_COMMANDS = {
  'stays:intervals': ['stays', 'intervals', slug],
  'stays:ingest': ['stays', 'ingest', slug],
};
const fitsShape = (tail, shape) =>
  tail.length === shape.length && shape.every((want, i) => (want instanceof RegExp ? want.test(tail[i]) : tail[i] === want));
const gKindArgv = (kind, argv) => {
  gArgv(argv);
  const shapes = KIND_COMMANDS[kind];
  if (!shapes || !shapes.some((shape) => fitsShape(argv.slice(1), shape))) {
    throw new Error(`plan-trip: command ${JSON.stringify(argv)} is off the ${kind} allowlist for trip ${slug}`);
  }
  return argv;
};
const gStepArgv = (kind, step) => {
  gArgv(step.command);
  const shape = STEP_COMMANDS[`${kind}:${step.name}`];
  if (!shape || !fitsShape(step.command.slice(1), shape)) {
    throw new Error(`plan-trip: step ${step.name} command ${JSON.stringify(step.command)} is off the ${kind} allowlist for trip ${slug}`);
  }
  return step.command;
};
const uniq = (xs) => [...new Set(xs)];

const uvCmd = (argv) => `uv run --project ${project} ${gArgv(argv).join(' ')}`;
const commandFor = (node) => {
  let cmd = uvCmd(node.command);
  if (node.quota_cost > 0) cmd += ` --quota-floor ${quotaFloor}`;
  if (refresh && node.kind === 'sweep') cmd += ' --refresh';
  return cmd;
};

// Routing is enforced, not decorative; terra rides agentType (Workflow model opts take only
// Claude models); fable never runs a trip-planning subagent.
const MECHANICAL = { model: 'sonnet', effort: 'low' };
const TRIP_MODELS = new Set(['sonnet', 'haiku', 'opus']);
const routed = (node) => {
  const r = node.routing;
  if (!r || !TRIP_MODELS.has(r.model)) {
    throw new Error(`plan-trip: node ${node.id} routes model ${r && r.model} — trip-planning subagents run sonnet, haiku, or opus (terra rides agentType); fable never runs one`);
  }
  return { model: r.model, effort: r.effort };
};
const research = (node) => (researchLane === 'terra' ? { agentType: 'codex:codex-wrapper' } : routed(node));

// Collectors have no graph node and so no checkpoint; they re-run whenever assess does.
const COLLECTOR_PROMPTS = {
  verify:
    `Verify the business hard product behind each composed journey's award legs — WebSearch plus the getaway commands named here only, zero seats.aero quota.\n` +
    `Read the composed journeys:\n${CLI} trip artifact read ${slug} expand.json\n` +
    `For each award leg (mode "award") with a business ("J") segment in its detail, confirm the operating carrier and aircraft:\n` +
    `${CLI} quality classify --airline <carrier> --aircraft <aircraft>\n` +
    `When the verdict is "verify" or the segment is unclassified, WebSearch the carrier's seat map for that flight and date, recent cabin reviews, and retrofit trackers to pin the hard product.\n` +
    `Shape verify: an array of {id (the award leg's availability id), product (one of suite, solid, dated, barely, unknown — "unknown" when sources disagree), note (the product name plus one clause on the hard product)}.\n` +
    `Write it as {"verify": [...]} via this command, feeding the JSON on standard input:\n${CLI} trip artifact write ${slug} evidence-verify.json\n` +
    `Return {"ok": true, "count": <entries written>}.`,
  cash:
    `Flag cash-fare anomalies for the composed journeys — WebSearch or fli only, zero seats.aero quota, plus the getaway commands named here.\n` +
    `Read the composed journeys:\n${CLI} trip artifact read ${slug} expand.json\n` +
    `For each journey's award route, compare the current one-way cash fare in its cabin against what is typical for that route and season; a fare far below typical for the cabin ("unusually cheap for J") is the signal.\n` +
    `Shape cash: an array of {id (the award leg's availability id), route (origin-dest), cabin, quoted (number or null), typical (number or null), currency, anomaly (true when unusually cheap for the cabin), note}.\n` +
    `Write it as {"cash": [...]} via this command, feeding the JSON on standard input:\n${CLI} trip artifact write ${slug} evidence-cash.json\n` +
    `Return {"ok": true, "count": <entries written>}.`,
  context:
    `Add destination context for each journey's effective destination — WebSearch only, zero seats.aero quota, plus the getaway commands named here.\n` +
    `Read the composed journeys:\n${CLI} trip artifact read ${slug} expand.json\n` +
    `The effective destination is the arrival airport of the last leg before the homeward ($origins-directed) leg — where the trip actually lands, past any intermediate connection; when no leg heads home, it is the final leg's arrival. For each unique effective destination across the composed journeys, research the trip window: typical weather and season, a short entry/visa note, how the place fits the trip vibe, and notable events in the window.\n` +
    `Shape context: an array of {dest, weather, visa, appeal, events}.\n` +
    `Write it as {"context": [...]} via this command, feeding the JSON on standard input:\n${CLI} trip artifact write ${slug} evidence-context.json\n` +
    `Return {"ok": true, "count": <entries written>}.`,
  transit:
    `Flag transit-visa and entry risk against the traveler's documents — WebSearch only, zero seats.aero quota, plus the getaway commands named here.\n` +
    `Read the traveler documents (passports, residency, standing visas):\n${CLI} prefs show\n` +
    `Read the composed journeys:\n${CLI} trip artifact read ${slug} expand.json\n` +
    `Within one award leg, the origin of every segment after the first is a same-ticket airside connection; a cash leg carries its own airside connection airports in its connections list. Check every cash-leg connection airport individually — never a single generic flag for a multi-stop hop. Where two consecutive legs meet, the airport where one leg arrives and the next departs is a landside self-transfer, an entry rather than airside transit.\n` +
    `For each unique connection airport determine transit (airside) visa risk; for each self-transfer airport determine entry risk. Prefer official government and airport sources.\n` +
    `Shape transit: an array of {airport, kind ("transit" or "entry"), risk ("none", "possible", or "required"), note}.\n` +
    `Write it as {"transit": [...]} via this command, feeding the JSON on standard input:\n${CLI} trip artifact write ${slug} evidence-transit.json\n` +
    `Return {"ok": true, "count": <entries written>}.`,
};

// ── Load ───────────────────────────────────────────────────────────────────
phase('Load');
const loaded = await agent(
  `Run these two commands and shape their JSON into one object — run no other getaway command.\n` +
  `1. ${CLI} trip explain ${slug}\n` +
  `   The compiled execution graph: nodes with dependency inputs, ready-made commands, model routing, requires, and per-node freshness.\n` +
  `2. ${CLI} trip show ${slug}\n` +
  `   The trip record; only judgment.factors matters here.\n\n` +
  `Return {"graph": command 1's JSON verbatim, "judgmentFactors": the keys of command 2's judgment.factors as an array of strings ([] when judgment.factors is absent)}.`,
  { label: 'load', phase: 'Load', schema: LOAD_SCHEMA, ...MECHANICAL },
);
const graph = loaded.graph;
if (graph.slug !== slug) throw new Error(`plan-trip: trip explain returned the graph for ${graph.slug}, expected ${slug}`);
const judgmentFactors = loaded.judgmentFactors;

// A trip shape the walker cannot express is a stop-and-check-back, never an improvisation.
const surprise = (finding, options, extra = {}) => ({ slug, status: 'shape_surprise', finding, options, ...extra });

const JUDGMENT_KINDS = new Set(['assess', 'stays', 'scout']);
const PREFLIGHTS = new Set(['rooms_session']);
for (const node of graph.nodes) {
  routed(node);
  if (Array.isArray(node.command)) {
    gKindArgv(node.kind, node.command);
  } else if (!JUDGMENT_KINDS.has(node.kind)) {
    return surprise(
      `node ${node.id} is agent-shaped (no emitted command) with kind "${node.kind}", which this walker has no judgment handler for`,
      [
        'teach plan-trip.js a judgment handler for this node kind',
        'author an ad-hoc walker per references/workflows.md that handles it',
        'drop the node from the plan via trip set and re-dispatch',
        'run the remaining graph by hand from the trip explain command lines',
      ],
      { node },
    );
  }
  for (const step of node.steps) gStepArgv(node.kind, step);
}
for (const req of graph.requires) {
  if (!PREFLIGHTS.has(req)) {
    return surprise(
      `the graph requires "${req}", which this walker has no preflight for`,
      [
        'teach plan-trip.js a preflight for this requirement',
        'satisfy the requirement by hand and author an ad-hoc walker per references/workflows.md',
        'edit the plan via trip set so compile stops requiring it',
      ],
    );
  }
}

// ── Preflight ──────────────────────────────────────────────────────────────
// Compile is pure — it emits requires; the walker verifies them before any dispatch.
if (graph.requires.length) {
  phase('Preflight');
  for (const req of graph.requires) {
    const session = await agent(
      `Verify the seeded rooms.aero browser session before any stays dispatch — read-only; no getaway command, no login attempt.\n` +
      `Using the agent-browser CLI against the session named "rooms" (agent-browser --session rooms), open https://rooms.aero and read the top navigation.\n` +
      `A logged-in Pro session shows the account email, a PRO badge, and a Logout link in the nav; an anonymous session shows Login.\n` +
      `Return {"loggedIn": <true only when the nav shows Logout>, "pro": <true only when the nav shows the PRO badge>}.`,
      { label: `preflight:${req}`, phase: 'Preflight', schema: PREFLIGHT_SCHEMA, ...MECHANICAL },
    );
    if (!session || session.loggedIn !== true || session.pro !== true) {
      throw new Error(
        `plan-trip: ${req} preflight failed — lodging is in scope but the "rooms" agent-browser session is not a logged-in Pro session. Seed cookies for rooms.aero AND seats.aero into one session, then re-dispatch.`,
      );
    }
  }
}

// ── The walk ───────────────────────────────────────────────────────────────
// Kahn levels over artifact dependencies; inputs with no producer in the graph are external
// and count satisfied.
const producers = {};
for (const node of graph.nodes) for (const out of node.outputs) producers[out] = node.id;
const levels = [];
{
  const placed = new Set();
  let pending = [...graph.nodes];
  while (pending.length) {
    const ready = pending.filter((n) => n.inputs.every((a) => !(a in producers) || placed.has(producers[a])));
    if (!ready.length) throw new Error(`plan-trip: dependency cycle among ${pending.map((n) => n.id).join(', ')}`);
    for (const n of ready) placed.add(n.id);
    pending = pending.filter((n) => !placed.has(n.id));
    levels.push(ready);
  }
}

let phaseMap = Object.fromEntries(graph.nodes.map((n) => [n.id, n.fresh ? 'fresh' : 'stale']));
const states = {};
const skipped = [];
const outcomes = new Map();
const evidenceFailed = [];
let okCollectors = [];
let evidenceDone = false;

const readPhaseMap = async (label, title) => {
  const res = await agent(
    `Run exactly this one command and return its phase map — run no other getaway command:\n` +
    `${CLI} trip status ${slug}\n` +
    `Return {"phaseMap": the status JSON's phase_map verbatim}.`,
    { label, phase: title, schema: STATUS_SCHEMA, ...MECHANICAL },
  );
  if (!res || typeof res !== 'object' || typeof res.phaseMap !== 'object' || res.phaseMap === null) {
    throw new Error(`plan-trip: status re-read (${label}) returned no phase map`);
  }
  return res.phaseMap;
};

const runNode = (node, title, prefix) => {
  const finalize = node.kind === 'finalize';
  return agent(
    `Run exactly this one command, then report honestly how it exited — run no other getaway command:\n` +
    `${commandFor(node)}\n` +
    (finalize
      ? `It writes finalists.json and prints it. Return {"exit_code": <the command's exit code, a number>, "journeys": <length of the printed journeys>, "unpaired_leads": <length of the printed unpaired_leads>, "notable_stretches": <length of the printed notable_stretches>}.`
      : `On success the CLI writes this node's artifacts and stamps its own checkpoint; your only job is the verbatim command and an honest exit report. Return {"exit_code": <the command's exit code, a number>}.`) +
    `\nIf the command exits nonzero, also include "stderr_tail": <the last ~20 lines of its stderr, verbatim as one newline-joined string> so the failure self-diagnoses.`,
    { label: `${prefix}${node.id}`, phase: title, schema: finalize ? FINALIZE_SCHEMA : RUN_SCHEMA, ...routed(node) },
  );
};

const runCollectors = async (node, title) => {
  const wanted = uniq(judgmentFactors.map((f) => COLLECTOR_OF[f]).filter(Boolean));
  if (!wanted.length) {
    okCollectors = [];
    return;
  }
  const dispatch = (c, prefix) =>
    agent(COLLECTOR_PROMPTS[c], { label: `${prefix}evidence:${c}`, phase: title, schema: EVIDENCE_SCHEMA, ...research(node) });
  const wrote = (r) => r !== null && typeof r === 'object' && r.ok === true;
  // Fan-out results are possibly-null: filter, retry the misses once, never dereference.
  const first = await parallel(wanted.map((c) => () => dispatch(c, '')));
  const missed = wanted.filter((_, i) => !wrote(first[i]));
  const second = await parallel(missed.map((c) => () => dispatch(c, 'retry:')));
  const stillMissed = missed.filter((_, i) => !wrote(second[i]));
  evidenceFailed.push(...stillMissed);
  okCollectors = wanted.filter((c) => !stillMissed.includes(c));
};

const runScout = (node, title, prefix) => {
  const leg = gLegId(node.leg);
  const artifact = `legs/${leg}/scout.json`;
  return agent(
    `Propose the destination hub airports for discover leg "${leg}" — zero seats.aero quota, run only the getaway commands named here.\n` +
    `Read the trip and find this leg's discover brief and its max_airports cap:\n${CLI} trip show ${slug}\n` +
    `The leg with id "${leg}" carries dests.discover.{brief, max_airports}. Research hub candidates that answer the brief — weigh season, award-space reputation across the mileage programs, and layover interest — and pick at most max_airports airports.\n` +
    `Assemble a list [{"airport": <3-letter IATA>, "why": <one sentence, at most 200 chars, on why this hub fits the brief>}] and write it via this command, feeding the JSON on standard input:\n` +
    `${CLI} trip artifact write ${slug} ${artifact}\n` +
    `The CLI rejects a non-IATA code, an over-cap list, or a missing field — fix and rewrite if it does. Then stamp the node by running exactly:\n${CLI} trip phase-done ${slug} scout:${leg}\n` +
    `Return {"ok": true, "airports": <count proposed>}.`,
    { label: `${prefix}scout:${leg}`, phase: title, schema: SCOUT_SCHEMA, ...research(node) },
  );
};

const runAssess = async (node, title, prefix) => {
  if (!evidenceDone) {
    await runCollectors(node, title);
    evidenceDone = true;
  }
  const evidenceReads = okCollectors.map((c) => `- ${CLI} trip artifact read ${slug} evidence-${c}.json`).join('\n');
  return agent(
    `Weigh every composed journey and produce assess.json — zero seats.aero quota, run only the getaway commands named here.\n` +
    `Read the trip's guidance and judged factors (judgment.guidance, and judgment.factors with their priority lanes):\n${CLI} trip show ${slug}\n` +
    `Read the traveler's layover preferences (layovers.style, layovers.min_connection_minutes, layovers.prefer_cities, layovers.avoid_cities):\n${CLI} prefs show\n` +
    `Read the composed journeys:\n${CLI} trip artifact read ${slug} expand.json\n` +
    (evidenceReads ? `Read the collected evidence:\n${evidenceReads}\n` : ``) +
    `\nJourneys carry typed legs: an "award" leg has expanded seat detail; a "cash" leg carries elapsed time and cost only, so a factor that needs cabin or seat detail has nothing to judge on it — that is neutral, never a demotion. Each journey already carries CLI-computed fit_facts and preference_misses: weigh them, never recompute them, and never let a preference gate — a miss orders and annotates.\n` +
    `Judge each factor per leg where it applies, weighing every leg symmetrically regardless of its position in the chain. The layovers factor follows the layover doctrine: judge each journey's layovers on their own merits. Under the traveler's comfortable-connection floor is risky-short (demote and name the margin). Floor to ~3h is comfortable (neutral). ~3-6h is dead time (mild demote, softened at airports that pass hours well like DOH, SIN, ICN). Over ~6h forks on style and city: an explore-style traveler with a city worth leaving the airport for is a promote; a minimize-style traveler, an avoid-listed city, or no feasible exit is a harder demote. Overnight gaps are dead-long unless explore-style and the city warrants it. A nonstop is neutral — never demote for having no layover.\n` +
    `Verdicts are "promote", "neutral", or "demote", each with one evidence sentence. Unknown is neutral — never demote what the evidence does not condemn.\n` +
    `Also select notable stretches: up to 2 journeys whose excellence outweighs a named preference miss ("back Tuesday, but perfect"), each {journey_id, why} with the miss named in why — rank surfaces the ones that fall beyond the presentation cut. [] when none stand out.\n` +
    `Assemble {"journeys": {<journey id>: {"verdicts": [{"factor", "leg", "verdict", "evidence"}]}}, "notable_stretches": [...]} and write it via this command, feeding the JSON on standard input:\n` +
    `${CLI} trip artifact write ${slug} assess.json\n` +
    `Then stamp the node by running exactly:\n${CLI} trip phase-done ${slug} assess\n` +
    `Return {"ok": true, "journeys": <count judged>, "notable_stretches": <count selected>}.`,
    { label: `${prefix}assess`, phase: title, schema: ASSESS_SCHEMA, ...research(node) },
  );
};

const runStays = async (node, title, prefix) => {
  const step = (name) => {
    const s = node.steps.find((x) => x.name === name);
    if (!s) throw new Error(`plan-trip: stays node is missing its "${name}" step`);
    return s.command;
  };
  const doc = await agent(
    `Run exactly this one offline command and return its JSON — run no other getaway command:\n` +
    `${uvCmd(step('intervals'))}\n` +
    `It derives the stays worklist from the ranked board. Return {"inputs_fp": the inputs_fp verbatim, "journeys": the journeys array verbatim}.`,
    { label: `${prefix}stays:intervals`, phase: title, schema: INTERVALS_SCHEMA, ...MECHANICAL },
  );
  if (!doc || !Array.isArray(doc.journeys)) throw new Error('plan-trip: stays intervals returned no worklist');
  const fp = gFp(doc.inputs_fp);
  const ingestCmd = `${uvCmd(step('ingest'))} --inputs-fp ${fp}`;

  // Dedupe identical searches by search_key; deferred journeys stay untouched — finalize
  // recomputes their deferral reasons, so they never enter the walk or the ingest document.
  const targets = new Map();
  for (const entry of doc.journeys) {
    if (entry.disposition !== 'walk') continue;
    const jid = gJourneyId(entry.journey_id);
    if (!targets.has(entry.search_key)) {
      const interval = entry.interval;
      targets.set(entry.search_key, {
        dest: gIata(entry.destination_airport),
        checkIn: gDate(interval.check_in),
        checkOut: gDate(interval.check_out),
        nights: gNights(interval.nights),
        nightClamped: interval.night_clamped === true,
        journeyIds: [],
      });
    }
    targets.get(entry.search_key).journeyIds.push(jid);
  }

  if (!targets.size) {
    return agent(
      `Every eligible journey defers lodging, so stamp the stays node with an empty document — run exactly this one command, feeding the JSON below on standard input verbatim:\n` +
      `${ingestCmd}\n` +
      `stdin:\n{"stays": {}}\n` +
      `Return {"exit_code": <the command's exit code, a number>}.`,
      { label: `${prefix}stays:ingest-empty`, phase: title, schema: RUN_SCHEMA, ...MECHANICAL },
    );
  }

  const lines = [...targets.values()]
    .map(
      (t, i) =>
        `${i + 1}. ${t.dest} — check-in ${t.checkIn}, check-out ${t.checkOut}, ${t.nights} night(s)` +
        (t.nightClamped ? ' [clamped to the rooms.aero 5-night cap; disclose night_clamped: true]' : '') +
        ` — journeys: ${t.journeyIds.join(', ')}`,
    )
    .join('\n');
  return agent(
    `Walk rooms.aero for hotel award availability at each stay target below — one seeded browser session, strictly sequential, zero seats.aero quota, and run only the getaway command named at the end.\n` +
    `Use the agent-browser session named "rooms" (already verified logged-in Pro). Never attempt a re-login or a live browser attach; if the session reads logged out mid-walk, record every remaining target with search_state "logged_out" and skip ahead to the ingest.\n\n` +
    `Stay targets (searches deduplicated; a target's entry lands verbatim under every listed journey id):\n${lines}\n\n` +
    `Per target, follow the observed rooms.aero drive protocol:\n` +
    `1. Geocode: GET https://rooms.aero/feapi/geocoding?q=<the destination city for the airport code> and take features[0] (place_name, center [lng, lat]); no features means search_state "geocode_miss".\n` +
    `2. Navigate the deep link: https://rooms.aero/search?city=<urlencoded place_name>&start=<check-in>&end=<check-in>&nights=<nights>&lat=<lat>&lng=<lng> — start=end pins the exact check-in block, and loading auto-runs the search.\n` +
    `3. Wait for the network to settle, then poll GET /feapi/revalidation/<revalidation_id from the search response> until queued is 0; give up after about 30 seconds and keep the stale rows.\n` +
    `4. Re-issue the page's POST /feapi/search from page context (same bbox body) and read the hotels JSON; scrape the result cards from the DOM only when /feapi fails. An empty hotels list is search_state "searched_empty"; a WAF challenge or repeated non-200 is "bot_wall" — back off once, then record it and move on.\n\n` +
    `Normalize each target into one stays entry:\n` +
    `{"interval": {"check_in", "check_out", "nights"}, "destination": {"query", "center": {"lat", "lng"}, "viewport": {"sw_lat", "sw_lng", "ne_lat", "ne_lng"}, "airport"}, "provenance": {"source": "rooms.aero", "session": "pro", "fetched_at": <UTC now>, "search_url", "revalidation": {"total", "successful", "queued"} or null, "night_clamped"}, "rooms": [per hotel: {"rooms_aero_id", "program" (the row's source slug), "name", "lat", "lng", "currency" (property-local), "last_checked_at" (the row's real-UTC timestamp), "stale" (true when last_checked_at is more than 24 hours before fetched_at), "offers": [per award class with data: {"award_class": "standard" or "suite", "check_in" (the block's check-in date), "nights", "award_points_per_night" (integer or null), "cash_per_night_cents" (integer cents or null), "cents_per_point" (float or null)}]}], "search_state": one of complete, searched_empty, night_clamped, bot_wall, logged_out, date_in_past, geocode_miss, failed}\n` +
    `Cash is integer cents of the row's own currency_code; points are integers; per-night rates are the source of truth — never total a stay.\n\n` +
    `Assemble ONE document {"stays": {<journey id>: [<entry>, ...]}} — each journey id maps to the list of its stay entries, one per stay interval; append a shared target's entry verbatim to each of its journey ids' lists (a single-destination trip yields a one-element list), then pipe it on standard input to exactly this command:\n` +
    `${ingestCmd}\n` +
    `It validates the shape, writes stays.json, and stamps the stays node.\n` +
    `Return {"ok": true, "walked": <targets driven>, "ingested": <journey ids written>, "states": {<destination airport>: <search_state>}}.`,
    { label: `${prefix}stays:walk`, phase: title, schema: STAYS_WALK_SCHEMA, ...research(node) },
  );
};

const dispatchNode = (node, title, prefix) => {
  if (Array.isArray(node.command)) return runNode(node, title, prefix);
  if (node.kind === 'assess') return runAssess(node, title, prefix);
  if (node.kind === 'scout') return runScout(node, title, prefix);
  return runStays(node, title, prefix);
};

const exitedWith = (result, code) => result !== null && typeof result === 'object' && result.exit_code === code;
const stderrTail = (result) => (result !== null && typeof result === 'object' && typeof result.stderr_tail === 'string' ? result.stderr_tail : null);

// Exit 1 on a quota-costed node is a quota stop, distinct from data failure: a partial artifact
// exists and the node is deliberately unstamped for a cache-first resume. Never retried.
const isQuotaStop = (node, result) => node.quota_cost > 0 && exitedWith(result, 1);

for (let li = 0; li < levels.length; li++) {
  const toRun = [];
  for (const node of levels[li]) {
    if (!refresh && phaseMap[node.id] === 'fresh') {
      states[node.id] = { state: 'skipped', reason: 'fresh' };
      skipped.push(node.id);
    } else {
      toRun.push(node);
    }
  }
  if (!toRun.length) continue;
  const title = uniq(toRun.map((n) => n.kind.charAt(0).toUpperCase() + n.kind.slice(1))).join(' + ');
  phase(title);

  const runnables = toRun.filter((n) => Array.isArray(n.command));
  const judgments = toRun.filter((n) => !Array.isArray(n.command));
  const results = runnables.length ? await pipeline(runnables, (n) => dispatchNode(n, title, '')) : [];
  runnables.forEach((n, i) => outcomes.set(n.id, results[i] === undefined ? null : results[i]));
  for (const n of judgments) outcomes.set(n.id, await dispatchNode(n, title, ''));

  const pendingNodes = [];
  for (const n of toRun) {
    if (isQuotaStop(n, outcomes.get(n.id))) states[n.id] = { state: 'not_run', reason: 'quota_floor' };
    else pendingNodes.push(n);
  }

  // Checkpoint state is the only truth: re-read, retry any unstamped node once — a null or
  // garbage result lands on the same path — then surface failed.
  phaseMap = await readPhaseMap(`status:${li}`, title);
  const retryable = [];
  for (const n of pendingNodes) {
    if (phaseMap[n.id] === 'fresh') states[n.id] = { state: 'done' };
    else retryable.push(n);
  }
  if (!retryable.length) continue;

  const retryRunnables = retryable.filter((n) => Array.isArray(n.command));
  // Snapshot attempt-1 outcomes before the retry overwrite below discards them: a retry that
  // returns null or prose must not erase the first attempt's stderr diagnostic.
  const firstAttempt = new Map(retryable.map((n) => [n.id, outcomes.get(n.id)]));
  // Exit 3 is a transient CLI state-conflict; back off once so the sole retry clears the window.
  if (retryRunnables.some((n) => exitedWith(outcomes.get(n.id), 3))) await sleep(exit3BackoffMs);
  const retryResults = retryRunnables.length ? await pipeline(retryRunnables, (n) => dispatchNode(n, title, 'retry:')) : [];
  retryRunnables.forEach((n, i) => outcomes.set(n.id, retryResults[i] === undefined ? null : retryResults[i]));
  for (const n of retryable.filter((x) => !Array.isArray(x.command))) outcomes.set(n.id, await dispatchNode(n, title, 'retry:'));

  phaseMap = await readPhaseMap(`status:${li}:retry`, title);
  for (const n of retryable) {
    if (isQuotaStop(n, outcomes.get(n.id))) states[n.id] = { state: 'not_run', reason: 'quota_floor' };
    else if (phaseMap[n.id] === 'fresh') states[n.id] = { state: 'done' };
    else {
      const failed = { state: 'failed', reason: 'node unstamped after one retry' };
      const tail = stderrTail(outcomes.get(n.id)) ?? stderrTail(firstAttempt.get(n.id));
      if (tail !== null) failed.stderr_tail = tail;
      states[n.id] = failed;
    }
  }
}

// ── Report ─────────────────────────────────────────────────────────────────
const finalizeNode = graph.nodes.find((n) => n.kind === 'finalize');
const finalizeOutcome = finalizeNode && states[finalizeNode.id] && states[finalizeNode.id].state === 'done' ? outcomes.get(finalizeNode.id) : null;
const board = finalizeOutcome !== null && typeof finalizeOutcome === 'object' ? finalizeOutcome : null;
const count = (s) => Object.values(states).filter((v) => v.state === s).length;
log(
  `plan-trip ${slug}: ${count('done')} done, ${count('skipped')} fresh-skipped, ${count('not_run')} not_run, ${count('failed')} failed` +
  (board ? `; ${board.journeys} journey(s), ${board.unpaired_leads} lead(s), ${board.notable_stretches} stretch(es)` : ''),
);
return {
  slug,
  status: 'complete',
  trip_type: graph.trip_type,
  nodes: states,
  skipped,
  evidence_failed: evidenceFailed,
  journeys: board ? board.journeys : null,
  unpaired_leads: board ? board.unpaired_leads : null,
  notable_stretches: board ? board.notable_stretches : null,
};
