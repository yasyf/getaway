import assert from 'node:assert/strict';
import { test } from 'node:test';

import { loadWorkflow, makeHarness, runWorkflow } from './harness.mjs';

const PROJECT = '/Users/yasyf/Code/getaway/cli';
const SLUG = 'my-trip';
const ARGS = { project: PROJECT, slug: SLUG };
const FP = 'a'.repeat(64);

const RUNNER = { model: 'sonnet', effort: 'low' };
const RESEARCH = { model: 'opus', effort: 'xhigh' };

// Node fixtures mirror `trip compile` output verbatim (verified against a live compile).
const N = (id, kind, o = {}) => ({
  id,
  kind,
  scope: o.scope ?? 'leg',
  leg: o.leg ?? null,
  inputs: o.inputs ?? [],
  outputs: o.outputs ?? [],
  ttl_hours: o.ttl ?? null,
  quota_cost: o.quota ?? 0,
  routing: o.routing ?? RUNNER,
  requires: o.requires ?? [],
  command: o.command ?? null,
  steps: o.steps ?? [],
  endpoint_source: o.endpoint_source ?? null,
  fresh: o.fresh ?? false,
});

const sweepNode = (label, o = {}) =>
  N(`sweep:outbound:${label}`, 'sweep', {
    leg: 'outbound',
    outputs: [`legs/outbound/sweep-${label}.json`],
    command: ['getaway', 'sweep', 'run', SLUG, `outbound:${label}`],
    quota: 3,
    ttl: 24,
    ...o,
  });

const staysNode = (o = {}) =>
  N('stays', 'stays', {
    scope: 'journey',
    inputs: ['rank.json'],
    outputs: ['stays.json'],
    routing: RESEARCH,
    requires: ['rooms_session'],
    steps: [
      { name: 'intervals', command: ['getaway', 'stays', 'intervals', SLUG] },
      { name: 'ingest', command: ['getaway', 'stays', 'ingest', SLUG] },
    ],
    ...o,
  });

const oneWayGraph = (o = {}) => ({
  slug: o.slug ?? SLUG,
  trip_type: 'one_way',
  lodging: false,
  requires: o.requires ?? [],
  quota_budget: { total: 15, nodes: [] },
  nodes: [
    sweepNode('asia-beach', { fresh: o.fresh }),
    N('shortlist:outbound', 'shortlist', {
      leg: 'outbound',
      inputs: ['legs/outbound/sweep-asia-beach.json'],
      outputs: ['legs/outbound/shortlist.json'],
      command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'outbound'],
      fresh: o.fresh,
    }),
    N('expand', 'expand', {
      scope: 'journey',
      inputs: ['legs/outbound/shortlist.json'],
      outputs: ['expand.json'],
      command: ['getaway', 'expand', 'run', SLUG],
      quota: 12,
      ttl: 6,
      fresh: o.fresh,
    }),
    N('assess', 'assess', { scope: 'journey', inputs: ['expand.json'], outputs: ['assess.json'], routing: o.assessRouting ?? RESEARCH, fresh: o.fresh }),
    N('rank', 'rank', {
      scope: 'journey',
      inputs: ['legs/outbound/shortlist.json', 'expand.json', 'assess.json'],
      outputs: ['rank.json'],
      command: ['getaway', 'rank', SLUG],
      fresh: o.fresh,
    }),
    N('finalize', 'finalize', {
      scope: 'journey',
      inputs: ['rank.json'],
      outputs: ['finalists.json'],
      command: ['getaway', 'trip', 'finalize', SLUG],
      fresh: o.fresh,
    }),
  ],
});

const roundTripGraph = ({ lodging = false, openJaw = false, fresh = false } = {}) => {
  const obShortlist = 'legs/outbound/shortlist.json';
  const retShortlist = 'legs/return/shortlist.json';
  const nodes = [
    sweepNode('asia-beach', { fresh }),
    sweepNode('warm', { fresh }),
    N('shortlist:outbound', 'shortlist', {
      leg: 'outbound',
      inputs: ['legs/outbound/sweep-asia-beach.json', 'legs/outbound/sweep-warm.json'],
      outputs: [obShortlist],
      command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'outbound'],
      fresh,
    }),
    N('sweep:return', 'sweep', {
      leg: 'return',
      inputs: [obShortlist],
      outputs: ['legs/return/sweep.json'],
      command: ['getaway', 'sweep', 'run', SLUG, 'return'],
      quota: 3,
      ttl: 24,
      endpoint_source: { from: obShortlist, field: 'dest', union: [], override: openJaw ? { origins: ['KIX'] } : null },
      fresh,
    }),
    N('shortlist:return', 'shortlist', {
      leg: 'return',
      inputs: ['legs/return/sweep.json'],
      outputs: [retShortlist],
      command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'return'],
      fresh,
    }),
    N('expand', 'expand', {
      scope: 'journey',
      inputs: [obShortlist, retShortlist],
      outputs: ['expand.json'],
      command: ['getaway', 'expand', 'run', SLUG],
      quota: 12,
      ttl: 6,
      fresh,
    }),
    N('assess', 'assess', { scope: 'journey', inputs: ['expand.json'], outputs: ['assess.json'], routing: RESEARCH, fresh }),
    N('rank', 'rank', {
      scope: 'journey',
      inputs: [obShortlist, retShortlist, 'expand.json', 'assess.json'],
      outputs: ['rank.json'],
      command: ['getaway', 'rank', SLUG],
      fresh,
    }),
    N('finalize', 'finalize', {
      scope: 'journey',
      inputs: lodging ? ['rank.json', 'stays.json'] : ['rank.json'],
      outputs: ['finalists.json'],
      command: ['getaway', 'trip', 'finalize', SLUG],
      fresh,
    }),
    ...(lodging ? [staysNode({ fresh })] : []),
  ];
  return {
    slug: SLUG,
    trip_type: 'round_trip',
    lodging,
    requires: lodging ? ['rooms_session'] : [],
    quota_budget: { total: 33, nodes: [] },
    nodes,
  };
};

// A gateway hybrid: an either-mode onward leg (award sweep/shortlist + cash pairs/bridge) chained
// between the award outbound and the $origins return. Mirrors a live `trip compile` verbatim.
const hybridLodgingGraph = () => {
  const graph = roundTripGraph({ lodging: true });
  const obShortlist = 'legs/outbound/shortlist.json';
  const onwardSweep = 'legs/onward/sweep.json';
  const onwardShortlist = 'legs/onward/shortlist.json';
  const onwardPairs = 'legs/onward/onward.json';
  const onwardBridge = 'legs/onward/bridge.json';
  const fromOutbound = { from: obShortlist, field: 'dest', union: [], override: { dests: ['OKA', 'KIX'] } };
  const at = graph.nodes.findIndex((n) => n.id === 'shortlist:outbound') + 1;
  graph.nodes.splice(
    at,
    0,
    N('sweep:onward', 'sweep', {
      leg: 'onward',
      inputs: [obShortlist],
      outputs: [onwardSweep],
      command: ['getaway', 'sweep', 'run', SLUG, 'onward'],
      quota: 3,
      ttl: 24,
      endpoint_source: fromOutbound,
    }),
    N('shortlist:onward', 'shortlist', {
      leg: 'onward',
      inputs: [onwardSweep],
      outputs: [onwardShortlist],
      command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'onward'],
    }),
    N('pairs:onward', 'onward', {
      leg: 'onward',
      inputs: [obShortlist, onwardSweep],
      outputs: [onwardPairs],
      command: ['getaway', 'shortlist', 'onward', SLUG, '--leg', 'onward'],
      endpoint_source: fromOutbound,
    }),
    N('bridge:onward', 'bridge', {
      leg: 'onward',
      inputs: [onwardPairs],
      outputs: [onwardBridge],
      command: ['getaway', 'bridge', SLUG, '--leg', 'onward'],
      ttl: 24,
    }),
  );
  // The return now chains off the onward leg's reached dests, not the outbound's.
  const retSweep = graph.nodes.find((n) => n.id === 'sweep:return');
  retSweep.inputs = [onwardShortlist];
  retSweep.endpoint_source = { from: onwardShortlist, field: 'dest', union: ['OKA', 'KIX'], override: null };
  const expand = graph.nodes.find((n) => n.id === 'expand');
  expand.inputs = ['legs/outbound/shortlist.json', onwardShortlist, 'legs/return/shortlist.json', onwardPairs, onwardBridge];
  const rank = graph.nodes.find((n) => n.id === 'rank');
  rank.inputs = [obShortlist, onwardShortlist, 'legs/return/shortlist.json', 'expand.json', 'assess.json'];
  return graph;
};

// A three-leg award chain with stay boundaries (SFO→NRT, NRT→BKK, BKK→$origins): every leg carries
// its own bare-id sweep + shortlist, each chaining off the prior leg's reached dests.
const multiCityGraph = () => {
  const obShortlist = 'legs/outbound/shortlist.json';
  const hopShortlist = 'legs/hop/shortlist.json';
  const retShortlist = 'legs/return/shortlist.json';
  return {
    slug: SLUG,
    trip_type: 'round_trip',
    lodging: false,
    requires: [],
    quota_budget: { total: 39, nodes: [] },
    nodes: [
      N('sweep:outbound', 'sweep', {
        leg: 'outbound',
        outputs: ['legs/outbound/sweep.json'],
        command: ['getaway', 'sweep', 'run', SLUG, 'outbound'],
        quota: 9,
        ttl: 24,
      }),
      N('shortlist:outbound', 'shortlist', {
        leg: 'outbound',
        inputs: ['legs/outbound/sweep.json'],
        outputs: [obShortlist],
        command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'outbound'],
      }),
      N('sweep:hop', 'sweep', {
        leg: 'hop',
        inputs: [obShortlist],
        outputs: ['legs/hop/sweep.json'],
        command: ['getaway', 'sweep', 'run', SLUG, 'hop'],
        quota: 9,
        ttl: 24,
        endpoint_source: { from: obShortlist, field: 'dest', union: [], override: { dests: ['BKK'] } },
      }),
      N('shortlist:hop', 'shortlist', {
        leg: 'hop',
        inputs: ['legs/hop/sweep.json'],
        outputs: [hopShortlist],
        command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'hop'],
      }),
      N('sweep:return', 'sweep', {
        leg: 'return',
        inputs: [hopShortlist],
        outputs: ['legs/return/sweep.json'],
        command: ['getaway', 'sweep', 'run', SLUG, 'return'],
        quota: 9,
        ttl: 24,
        endpoint_source: { from: hopShortlist, field: 'dest', union: [], override: null },
      }),
      N('shortlist:return', 'shortlist', {
        leg: 'return',
        inputs: ['legs/return/sweep.json'],
        outputs: [retShortlist],
        command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'return'],
      }),
      N('expand', 'expand', {
        scope: 'journey',
        inputs: [obShortlist, hopShortlist, retShortlist],
        outputs: ['expand.json'],
        command: ['getaway', 'expand', 'run', SLUG],
        quota: 12,
        ttl: 6,
      }),
      N('assess', 'assess', { scope: 'journey', inputs: ['expand.json'], outputs: ['assess.json'], routing: RESEARCH }),
      N('rank', 'rank', {
        scope: 'journey',
        inputs: [obShortlist, hopShortlist, retShortlist, 'expand.json', 'assess.json'],
        outputs: ['rank.json'],
        command: ['getaway', 'rank', SLUG],
      }),
      N('finalize', 'finalize', {
        scope: 'journey',
        inputs: ['rank.json'],
        outputs: ['finalists.json'],
        command: ['getaway', 'trip', 'finalize', SLUG],
      }),
    ],
  };
};

// A leading cash positioning leg (SFO→LAX, priced via pairs+bridge) feeding an award onward leg to
// the deep destination — an open jaw with no homeward leg. The positioning leg has no sweep.
const positioningGraph = () => {
  const posPairs = 'legs/positioning/onward.json';
  const posBridge = 'legs/positioning/bridge.json';
  const onwardShortlist = 'legs/onward/shortlist.json';
  return {
    slug: SLUG,
    trip_type: 'open_jaw',
    lodging: false,
    requires: [],
    quota_budget: { total: 21, nodes: [] },
    nodes: [
      N('pairs:positioning', 'onward', {
        leg: 'positioning',
        outputs: [posPairs],
        command: ['getaway', 'shortlist', 'onward', SLUG, '--leg', 'positioning'],
        endpoint_source: { field: 'dest', union: ['LAX'], override: { dests: ['NRT'] } },
      }),
      N('bridge:positioning', 'bridge', {
        leg: 'positioning',
        inputs: [posPairs],
        outputs: [posBridge],
        command: ['getaway', 'bridge', SLUG, '--leg', 'positioning'],
        ttl: 24,
      }),
      N('sweep:onward', 'sweep', {
        leg: 'onward',
        outputs: ['legs/onward/sweep.json'],
        command: ['getaway', 'sweep', 'run', SLUG, 'onward'],
        quota: 9,
        ttl: 24,
        endpoint_source: { field: 'dest', union: ['LAX'], override: { dests: ['NRT'] } },
      }),
      N('shortlist:onward', 'shortlist', {
        leg: 'onward',
        inputs: ['legs/onward/sweep.json'],
        outputs: [onwardShortlist],
        command: ['getaway', 'shortlist', 'run', SLUG, '--leg', 'onward'],
      }),
      N('expand', 'expand', {
        scope: 'journey',
        inputs: [onwardShortlist, posPairs, posBridge],
        outputs: ['expand.json'],
        command: ['getaway', 'expand', 'run', SLUG],
        quota: 12,
        ttl: 6,
      }),
      N('assess', 'assess', { scope: 'journey', inputs: ['expand.json'], outputs: ['assess.json'], routing: RESEARCH }),
      N('rank', 'rank', {
        scope: 'journey',
        inputs: [onwardShortlist, 'expand.json', 'assess.json'],
        outputs: ['rank.json'],
        command: ['getaway', 'rank', SLUG],
      }),
      N('finalize', 'finalize', {
        scope: 'journey',
        inputs: ['rank.json'],
        outputs: ['finalists.json'],
        command: ['getaway', 'trip', 'finalize', SLUG],
      }),
    ],
  };
};

const WORKLIST = [
  {
    journey_id: 'outbound:AV1:J|return:AV9:J',
    destination_airport: 'OKA',
    disposition: 'walk',
    interval: { check_in: '2026-09-10', check_out: '2026-09-14', nights: 4, night_clamped: false },
    search_key: 'OKA|2026-09-10|4',
    lodging_search: null,
  },
  {
    journey_id: 'outbound:AV2:J|return:AV8:J',
    destination_airport: 'OKA',
    disposition: 'walk',
    interval: { check_in: '2026-09-10', check_out: '2026-09-14', nights: 4, night_clamped: false },
    search_key: 'OKA|2026-09-10|4',
    lodging_search: null,
  },
  {
    journey_id: 'outbound:AV3:J',
    destination_airport: 'USM',
    disposition: 'deferred',
    interval: null,
    search_key: null,
    lodging_search: { state: 'deferred', reason: 'no_checkout' },
  },
];

const mkState = () => ({ done: new Set() });
const runStub = (state) => (opts) => {
  state.done.add(opts.label.replace(/^retry:/, ''));
  return { exit_code: 0 };
};
const mkStatus = (state, graph) => () => ({
  phaseMap: Object.fromEntries(graph.nodes.map((n) => [n.id, state.done.has(n.id) ? 'fresh' : 'stale'])),
});

const baseScript = (graph, state, over = {}) => ({
  load: { graph, judgmentFactors: over.judgmentFactors ?? [] },
  'status:*': mkStatus(state, graph),
  'sweep:*': runStub(state),
  'shortlist:*': runStub(state),
  'pairs:*': runStub(state),
  'bridge:*': runStub(state),
  expand: runStub(state),
  rank: runStub(state),
  finalize: () => {
    state.done.add('finalize');
    return { exit_code: 0, journeys: 4, unpaired_leads: 1, notable_stretches: 1 };
  },
  assess: () => {
    state.done.add('assess');
    return { ok: true, journeys: 4, notable_stretches: 1 };
  },
  'evidence:*': { ok: true, count: 1 },
  'preflight:rooms_session': { loggedIn: true, pro: true },
  'stays:intervals': { inputs_fp: FP, journeys: WORKLIST },
  'stays:walk': () => {
    state.done.add('stays');
    return { ok: true, walked: 1, ingested: 2, states: { OKA: 'complete' } };
  },
  'stays:ingest-empty': () => {
    state.done.add('stays');
    return { exit_code: 0 };
  },
  ...over,
});

const findCall = (calls, label) => calls.find((c) => c.opts.label === label);
const runRejects = async (args, script, pattern) => {
  const fn = loadWorkflow();
  const h = makeHarness(script);
  await assert.rejects(fn(args, h.agent, h.pipeline, h.parallel, h.phase, h.log), pattern);
  return h;
};
const BOOKKEEPING = ['load', 'preflight:rooms_session', 'stays:intervals', 'stays:ingest-empty'];
const isBookkeeping = (label) => BOOKKEEPING.includes(label) || label.startsWith('status:');

// ── Happy paths per plan variant ───────────────────────────────────────────

test('one-way: every runner executes its emitted command verbatim, in dependency order', async () => {
  const state = mkState();
  const { result, calls, labels } = await runWorkflow(ARGS, baseScript(oneWayGraph(), state));

  assert.strictEqual(result.status, 'complete');
  for (const id of ['sweep:outbound:asia-beach', 'shortlist:outbound', 'expand', 'assess', 'rank', 'finalize']) {
    assert.deepStrictEqual(result.nodes[id], { state: 'done' });
  }
  const order = labels();
  assert.ok(order.indexOf('sweep:outbound:asia-beach') < order.indexOf('shortlist:outbound'));
  assert.ok(order.indexOf('shortlist:outbound') < order.indexOf('expand'));
  assert.ok(order.indexOf('expand') < order.indexOf('assess'));
  assert.ok(order.indexOf('assess') < order.indexOf('rank'));
  assert.ok(order.indexOf('rank') < order.indexOf('finalize'));

  assert.match(
    findCall(calls, 'sweep:outbound:asia-beach').prompt,
    /uv run --project \/Users\/yasyf\/Code\/getaway\/cli getaway sweep run my-trip outbound:asia-beach --quota-floor 100\n/,
  );
  assert.match(findCall(calls, 'expand').prompt, /getaway expand run my-trip --quota-floor 100\n/);
  assert.doesNotMatch(findCall(calls, 'rank').prompt, /--quota-floor/);
  assert.strictEqual(result.journeys, 4);
  assert.strictEqual(result.unpaired_leads, 1);
  assert.deepStrictEqual(result.skipped, []);
  assert.ok(!labels().some((l) => l.startsWith('stays:') || l.startsWith('preflight:')));
});

test('round-trip: return nodes dispatch only after the outbound shortlist settles', async () => {
  const state = mkState();
  const { result, labels } = await runWorkflow(ARGS, baseScript(roundTripGraph(), state));

  assert.strictEqual(result.status, 'complete');
  assert.strictEqual(result.trip_type, 'round_trip');
  const order = labels();
  assert.ok(order.indexOf('shortlist:outbound') < order.indexOf('sweep:return'));
  assert.ok(order.indexOf('sweep:return') < order.indexOf('shortlist:return'));
  assert.ok(order.indexOf('shortlist:return') < order.indexOf('expand'));
  assert.deepStrictEqual(result.nodes['sweep:return'], { state: 'done' });
  assert.deepStrictEqual(result.nodes['shortlist:return'], { state: 'done' });
});

test('round-trip + lodging: preflight gates before any dispatch, stays walks, finalize follows', async () => {
  const state = mkState();
  const { result, calls, labels } = await runWorkflow(ARGS, baseScript(roundTripGraph({ lodging: true }), state));

  assert.strictEqual(result.status, 'complete');
  const order = labels();
  assert.strictEqual(order[0], 'load');
  assert.strictEqual(order[1], 'preflight:rooms_session');
  assert.ok(order.indexOf('rank') < order.indexOf('stays:intervals'));
  assert.ok(order.indexOf('stays:walk') < order.indexOf('finalize'));
  assert.deepStrictEqual(result.nodes.stays, { state: 'done' });
  assert.match(findCall(calls, 'stays:walk').prompt, new RegExp(`getaway stays ingest my-trip --inputs-fp ${FP}`));
});

test('open-jaw: a return-override graph walks with identical mechanics', async () => {
  const state = mkState();
  const { result, called } = await runWorkflow(ARGS, baseScript(roundTripGraph({ openJaw: true }), state));

  assert.strictEqual(result.status, 'complete');
  assert.ok(called('sweep:return'));
  for (const [id, s] of Object.entries(result.nodes)) assert.strictEqual(s.state, 'done', `${id} should be done`);
});

test('multi-city: each leg sweeps only after the prior leg shortlists, whole chain walks to done', async () => {
  const state = mkState();
  const { result, calls, labels } = await runWorkflow(ARGS, baseScript(multiCityGraph(), state));

  assert.strictEqual(result.status, 'complete');
  assert.strictEqual(result.trip_type, 'round_trip');
  const order = labels();
  assert.ok(order.indexOf('shortlist:outbound') < order.indexOf('sweep:hop'));
  assert.ok(order.indexOf('shortlist:hop') < order.indexOf('sweep:return'));
  assert.ok(order.indexOf('shortlist:return') < order.indexOf('expand'));
  assert.match(findCall(calls, 'sweep:hop').prompt, /getaway sweep run my-trip hop --quota-floor 100\n/);
  for (const [id, s] of Object.entries(result.nodes)) assert.strictEqual(s.state, 'done', `${id} should be done`);
});

test('positioning: a leading cash leg (pairs+bridge) and the award onward leg both walk to done', async () => {
  const state = mkState();
  const { result, calls, labels } = await runWorkflow(ARGS, baseScript(positioningGraph(), state));

  assert.strictEqual(result.status, 'complete');
  assert.strictEqual(result.trip_type, 'open_jaw');
  const order = labels();
  assert.ok(order.indexOf('pairs:positioning') < order.indexOf('bridge:positioning'));
  assert.ok(order.indexOf('sweep:onward') < order.indexOf('shortlist:onward'));
  assert.ok(order.indexOf('bridge:positioning') < order.indexOf('expand'));
  assert.ok(order.indexOf('shortlist:onward') < order.indexOf('expand'));
  assert.match(findCall(calls, 'pairs:positioning').prompt, /getaway shortlist onward my-trip --leg positioning\n/);
  assert.match(findCall(calls, 'bridge:positioning').prompt, /getaway bridge my-trip --leg positioning\n/);
  for (const [id, s] of Object.entries(result.nodes)) assert.strictEqual(s.state, 'done', `${id} should be done`);
});

// ── Args adapter ───────────────────────────────────────────────────────────

test('args adapter: stringified args run end to end', async () => {
  const state = mkState();
  const { result } = await runWorkflow(JSON.stringify(ARGS), baseScript(oneWayGraph(), state));
  assert.strictEqual(result.slug, SLUG);
  assert.strictEqual(result.status, 'complete');
});

test('args adapter: a malformed args string throws with the raw value in the message', async () => {
  await assert.rejects(runWorkflow('definitely{not json', {}), /definitely\{not json/);
});

// ── Requires preflight ─────────────────────────────────────────────────────

test('preflight: a non-Pro rooms session fails loudly before any node dispatch', async () => {
  const state = mkState();
  const script = baseScript(roundTripGraph({ lodging: true }), state, {
    'preflight:rooms_session': { loggedIn: true, pro: false },
  });
  const h = await runRejects(ARGS, script, /rooms_session preflight failed/);
  assert.deepStrictEqual(h.labels(), ['load', 'preflight:rooms_session']);
});

test('preflight: an unknown requirement returns early with findings and options', async () => {
  const state = mkState();
  const graph = roundTripGraph();
  graph.requires = ['crystal_ball'];
  const { result, labels } = await runWorkflow(ARGS, baseScript(graph, state));

  assert.strictEqual(result.status, 'shape_surprise');
  assert.match(result.finding, /crystal_ball/);
  assert.ok(result.options.length >= 2 && result.options.length <= 4);
  assert.deepStrictEqual(labels(), ['load']);
});

// ── Return-early on unknown node shapes ────────────────────────────────────

test('shape surprise: an unknown agent-shaped node kind stops the walk with options, nothing dispatched', async () => {
  const state = mkState();
  const graph = oneWayGraph();
  graph.nodes.push(N('mystery', 'mystery', { scope: 'journey', inputs: ['expand.json'], outputs: ['mystery.json'], routing: RESEARCH }));
  const { result, labels } = await runWorkflow(ARGS, baseScript(graph, state));

  assert.strictEqual(result.status, 'shape_surprise');
  assert.match(result.finding, /mystery/);
  assert.strictEqual(result.node.id, 'mystery');
  assert.ok(result.options.length >= 2 && result.options.length <= 4);
  assert.deepStrictEqual(labels(), ['load']);
});

// ── Quota-floor semantics ──────────────────────────────────────────────────

test('quota floor: expand exiting 1 is not_run{quota_floor} — no retry, no fake warning, the walk continues', async () => {
  const state = mkState();
  const { result, labels, withPrefix } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { expand: { exit_code: 1 } }),
  );

  assert.deepStrictEqual(result.nodes.expand, { state: 'not_run', reason: 'quota_floor' });
  assert.strictEqual(withPrefix('retry:').length, 0);
  assert.ok(labels().includes('assess'));
  assert.ok(labels().includes('rank'));
  assert.ok(labels().includes('finalize'));
  assert.strictEqual(result.status, 'complete');
  assert.ok(!('warnings' in result));
});

// ── State trust: garbage and null results ──────────────────────────────────

test('garbage result: a prose runner leaves its node unstamped — one retry, then failed, never a silent pass', async () => {
  const state = mkState();
  const prose = 'Sure! I ran the command and everything went great.';
  const { result, withPrefix, labels } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { rank: prose, 'retry:rank': prose }),
  );

  assert.deepStrictEqual(result.nodes.rank, { state: 'failed', reason: 'node unstamped after one retry' });
  assert.strictEqual(withPrefix('retry:').length, 1);
  assert.ok(labels().includes('finalize'));
  assert.strictEqual(result.status, 'complete');
});

test('null result: a fan-out null filters into the retry path, never a dereference', async () => {
  const state = mkState();
  const { result, withPrefix } = await runWorkflow(
    ARGS,
    baseScript(roundTripGraph(), state, {
      'sweep:outbound:asia-beach': null,
      'retry:sweep:outbound:asia-beach': runStub(state),
    }),
  );

  assert.deepStrictEqual(result.nodes['sweep:outbound:asia-beach'], { state: 'done' });
  assert.deepStrictEqual(result.nodes['sweep:outbound:warm'], { state: 'done' });
  assert.strictEqual(withPrefix('retry:').length, 1);
  assert.strictEqual(result.status, 'complete');
});

test('null result: a thrown agent resolves to null through the harness fan-out and retries', async () => {
  const state = mkState();
  let first = true;
  const { result, withPrefix } = await runWorkflow(
    ARGS,
    baseScript(roundTripGraph(), state, {
      'sweep:outbound:asia-beach': (opts) => {
        if (first) {
          first = false;
          throw new Error('account limit');
        }
        return runStub(state)(opts);
      },
      'retry:sweep:outbound:asia-beach': runStub(state),
    }),
  );

  assert.deepStrictEqual(result.nodes['sweep:outbound:asia-beach'], { state: 'done' });
  assert.strictEqual(withPrefix('retry:').length, 1);
});

// ── Failure diagnostics & exit-3 backoff ───────────────────────────────────

test('failure report: a runner is asked for its stderr tail, and a failed node surfaces it verbatim', async () => {
  const state = mkState();
  const boom = { exit_code: 2, stderr_tail: 'Traceback (most recent call last):\nRuntimeError: rank.json is missing' };
  const { result, withPrefix, calls } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { rank: boom, 'retry:rank': boom }),
  );

  assert.deepStrictEqual(result.nodes.rank, {
    state: 'failed',
    reason: 'node unstamped after one retry',
    stderr_tail: 'Traceback (most recent call last):\nRuntimeError: rank.json is missing',
  });
  assert.strictEqual(withPrefix('retry:').length, 1);
  assert.match(findCall(calls, 'rank').prompt, /"stderr_tail": <the last ~20 lines/);
  assert.match(findCall(calls, 'finalize').prompt, /"stderr_tail": <the last ~20 lines/);
  assert.strictEqual(result.status, 'complete');
});

test('failure report: attempt-1 stderr survives a retry that returns null', async () => {
  const state = mkState();
  const boom = { exit_code: 2, stderr_tail: 'Traceback (most recent call last):\nRuntimeError: rank.json is missing' };
  const { result, withPrefix } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { rank: boom, 'retry:rank': null }),
  );

  assert.deepStrictEqual(result.nodes.rank, {
    state: 'failed',
    reason: 'node unstamped after one retry',
    stderr_tail: 'Traceback (most recent call last):\nRuntimeError: rank.json is missing',
  });
  assert.strictEqual(withPrefix('retry:').length, 1);
  assert.strictEqual(result.status, 'complete');
});

test('failure report: an unstamped node with no reported stderr stays a bare failure', async () => {
  const state = mkState();
  const prose = 'Sure! I ran the command and everything went great.';
  const { result } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { rank: prose, 'retry:rank': prose }),
  );

  assert.deepStrictEqual(result.nodes.rank, { state: 'failed', reason: 'node unstamped after one retry' });
});

test('exit-3 backoff: the wait lands via the injected seam before the sole retry dispatches', async () => {
  const state = mkState();
  const events = [];
  const sleep = (ms) => {
    events.push(`sleep:${ms}`);
    return Promise.resolve();
  };
  const { result, withPrefix } = await runWorkflow(
    { ...ARGS, sleep },
    baseScript(oneWayGraph(), state, {
      rank: { exit_code: 3, stderr_tail: 'state conflict: rank checkpoint locked' },
      'retry:rank': (opts) => {
        events.push('retry:rank');
        return runStub(state)(opts);
      },
    }),
  );

  assert.deepStrictEqual(events, ['sleep:60000', 'retry:rank']);
  assert.deepStrictEqual(result.nodes.rank, { state: 'done' });
  assert.strictEqual(withPrefix('retry:').length, 1);
  assert.strictEqual(result.status, 'complete');
});

for (const { id, first, backoffArg, expectSleeps } of [
  { id: 'exit 3 waits the default 60s once', first: { exit_code: 3, stderr_tail: 'lock' }, expectSleeps: [60000] },
  { id: 'exit 3 honors an overridden backoff', first: { exit_code: 3 }, backoffArg: 1500, expectSleeps: [1500] },
  { id: 'exit 2 retries immediately, never waiting', first: { exit_code: 2 }, expectSleeps: [] },
  { id: 'a fan-out null retries immediately, never waiting', first: null, expectSleeps: [] },
]) {
  test(`exit-3 backoff: ${id}`, async () => {
    const state = mkState();
    const sleeps = [];
    const args = { ...ARGS, sleep: (ms) => { sleeps.push(ms); return Promise.resolve(); } };
    if (backoffArg !== undefined) args.exit3BackoffMs = backoffArg;
    const { result, withPrefix } = await runWorkflow(
      args,
      baseScript(oneWayGraph(), state, { rank: first, 'retry:rank': runStub(state) }),
    );

    assert.deepStrictEqual(sleeps, expectSleeps);
    assert.deepStrictEqual(result.nodes.rank, { state: 'done' });
    assert.strictEqual(withPrefix('retry:').length, 1);
    assert.strictEqual(result.status, 'complete');
  });
}

for (const { id, overrides, pattern } of [
  { id: 'a non-integer backoff override fails loud at startup', overrides: { exit3BackoffMs: 1.5 }, pattern: /exit3BackoffMs must be a non-negative integer/ },
  { id: 'a negative backoff override fails loud at startup', overrides: { exit3BackoffMs: -1 }, pattern: /exit3BackoffMs must be a non-negative integer/ },
  { id: 'a non-function sleep override fails loud at startup', overrides: { sleep: 500 }, pattern: /sleep must be a function/ },
]) {
  test(`exit-3 backoff: ${id}`, async () => {
    await runRejects({ ...ARGS, ...overrides }, baseScript(oneWayGraph(), mkState()), pattern);
  });
}

// ── Routing enforcement (C2) ───────────────────────────────────────────────

test('routing: emitted routing lands on every agent — sonnet runners, opus research, sonnet bookkeeping', async () => {
  const state = mkState();
  const { calls } = await runWorkflow(
    ARGS,
    baseScript(hybridLodgingGraph(), state, {
      judgmentFactors: ['seat_quality', 'cash_anomaly', 'destination_context', 'transit_risk'],
    }),
  );

  assert.ok(calls.length > 0);
  for (const c of calls) {
    const label = c.opts.label.replace(/^retry:/, '');
    if (label === 'assess' || label === 'stays:walk' || label.startsWith('evidence:')) {
      assert.strictEqual(c.opts.model, 'opus', `${label} routes opus`);
      assert.strictEqual(c.opts.effort, 'xhigh', `${label} routes xhigh`);
    } else {
      assert.strictEqual(c.opts.model, 'sonnet', `${label} routes sonnet`);
      assert.strictEqual(c.opts.effort, 'low', `${label} routes low`);
    }
    assert.ok(!('agentType' in c.opts), `${label} carries no agentType on the opus lane`);
  }
  const research = calls.filter((c) => ['assess', 'stays:walk'].includes(c.opts.label) || c.opts.label.startsWith('evidence:'));
  assert.strictEqual(research.length, 6);
});

test('routing: the terra lane routes research by agentType, never a model opt', async () => {
  const state = mkState();
  const { calls } = await runWorkflow(
    { ...ARGS, researchLane: 'terra' },
    baseScript(hybridLodgingGraph(), state, { judgmentFactors: ['seat_quality'] }),
  );

  for (const c of calls) {
    const label = c.opts.label.replace(/^retry:/, '');
    if (label === 'assess' || label === 'stays:walk' || label.startsWith('evidence:')) {
      assert.strictEqual(c.opts.agentType, 'codex:codex-wrapper', `${label} routes by agentType`);
      assert.ok(!('model' in c.opts), `${label} carries no model on the terra lane`);
    } else {
      assert.strictEqual(c.opts.model, 'sonnet');
      assert.ok(!('agentType' in c.opts));
    }
  }
});

test('routing: a graph routing fable is rejected — fable never runs a trip-planning subagent', async () => {
  const state = mkState();
  const graph = oneWayGraph({ assessRouting: { model: 'fable', effort: 'xhigh' } });
  await runRejects(ARGS, baseScript(graph, state), /fable never runs one/);
});

// ── Freshness and refresh ──────────────────────────────────────────────────

test('fresh skip: an all-fresh graph dispatches nothing and reports every skip', async () => {
  const state = mkState();
  const { result, labels } = await runWorkflow(ARGS, baseScript(oneWayGraph({ fresh: true }), state));

  assert.deepStrictEqual(labels(), ['load']);
  assert.strictEqual(result.skipped.length, 6);
  for (const s of Object.values(result.nodes)) assert.deepStrictEqual(s, { state: 'skipped', reason: 'fresh' });
  assert.strictEqual(result.journeys, null);
  assert.strictEqual(result.status, 'complete');
});

test('refresh: fresh nodes re-run and --refresh reaches only sweep commands', async () => {
  const state = mkState();
  const { calls, result } = await runWorkflow(
    { ...ARGS, refresh: true },
    baseScript(oneWayGraph({ fresh: true }), state),
  );

  assert.deepStrictEqual(result.skipped, []);
  assert.match(findCall(calls, 'sweep:outbound:asia-beach').prompt, /sweep run my-trip outbound:asia-beach --quota-floor 100 --refresh\n/);
  assert.doesNotMatch(findCall(calls, 'shortlist:outbound').prompt, /--refresh/);
  assert.doesNotMatch(findCall(calls, 'rank').prompt, /--refresh/);
});

test('resume: a mid-run stale downstream node runs even though Load called it fresh', async () => {
  const state = mkState();
  const graph = oneWayGraph();
  graph.nodes.find((n) => n.id === 'expand').fresh = true;
  const { result, called } = await runWorkflow(ARGS, baseScript(graph, state));

  // The post-sweep status re-read reports expand stale (upstream re-ran), so the walker runs it.
  assert.ok(called('expand'));
  assert.deepStrictEqual(result.nodes.expand, { state: 'done' });
  assert.ok(!result.skipped.includes('expand'));
});

// ── Evidence collectors ────────────────────────────────────────────────────

test('evidence: collectors fan out only for judged factors that own one, and return is gone', async () => {
  const state = mkState();
  const { calls, labels } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { judgmentFactors: ['seat_quality', 'layovers'] }),
  );

  assert.ok(labels().includes('evidence:verify'));
  for (const gone of ['evidence:cash', 'evidence:context', 'evidence:transit', 'evidence:return']) {
    assert.ok(!labels().includes(gone), `${gone} must not dispatch`);
  }
  assert.match(findCall(calls, 'evidence:verify').prompt, /trip artifact write my-trip evidence-verify\.json/);
  assert.match(findCall(calls, 'assess').prompt, /trip artifact read my-trip evidence-verify\.json/);
});

test('evidence: the transit collector checks each cash-leg connection airport per-airport', async () => {
  const state = mkState();
  const { calls } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, { judgmentFactors: ['transit_risk'] }),
  );

  const transit = findCall(calls, 'evidence:transit').prompt;
  assert.match(transit, /a cash leg carries its own airside connection airports/);
  assert.match(transit, /Check every cash-leg connection airport individually/);
  // A multi-stop cash hop gets one check per airport, never one blanket flag.
  assert.match(transit, /never a single generic flag for a multi-stop hop/);
});

test('evidence: no judged factors means no collectors, and assess still runs', async () => {
  const state = mkState();
  const { calls, labels } = await runWorkflow(ARGS, baseScript(oneWayGraph(), state, { judgmentFactors: [] }));

  assert.ok(!labels().some((l) => l.startsWith('evidence:')));
  assert.ok(labels().includes('assess'));
  assert.doesNotMatch(findCall(calls, 'assess').prompt, /collected evidence/);
});

test('evidence: a collector failing twice drops from the assess reads and is reported', async () => {
  const state = mkState();
  const { result, calls } = await runWorkflow(
    ARGS,
    baseScript(oneWayGraph(), state, {
      judgmentFactors: ['seat_quality', 'transit_risk'],
      'evidence:verify': null,
      'retry:evidence:verify': null,
    }),
  );

  assert.deepStrictEqual(result.evidence_failed, ['verify']);
  const assess = findCall(calls, 'assess');
  assert.match(assess.prompt, /evidence-transit\.json/);
  assert.doesNotMatch(assess.prompt, /evidence-verify\.json/);
  assert.deepStrictEqual(result.nodes.assess, { state: 'done' });
});

// ── The assess contract ────────────────────────────────────────────────────

test('assess: the prompt carries the journey contract — misses weighed, stretches beyond the cut, node stamped', async () => {
  const state = mkState();
  const { calls } = await runWorkflow(ARGS, baseScript(oneWayGraph(), state));
  const assess = findCall(calls, 'assess');

  assert.match(assess.prompt, /trip artifact read my-trip expand\.json/);
  assert.match(assess.prompt, /getaway prefs show/);
  assert.match(assess.prompt, /layovers\.style/);
  assert.match(assess.prompt, /preference_misses/);
  assert.match(assess.prompt, /never let a preference gate/);
  assert.match(assess.prompt, /"cash" leg carries elapsed time and cost only/);
  assert.match(assess.prompt, /notable stretches: up to 2 journeys/);
  assert.match(assess.prompt, /beyond the presentation cut/);
  assert.match(assess.prompt, /trip phase-done my-trip assess/);
  assert.match(assess.prompt, /"verdicts": \[\{"factor", "leg", "verdict", "evidence"\}\]/);
});

// ── Stays dispatch ─────────────────────────────────────────────────────────

test('stays: the walk covers only disposition "walk", deduped by search_key; deferred journeys stay untouched', async () => {
  const state = mkState();
  const { calls, labels } = await runWorkflow(ARGS, baseScript(roundTripGraph({ lodging: true }), state));
  const walk = findCall(calls, 'stays:walk');

  assert.match(walk.prompt, /outbound:AV1:J\|return:AV9:J/);
  assert.match(walk.prompt, /outbound:AV2:J\|return:AV8:J/);
  assert.match(walk.prompt, /^1\. OKA — check-in 2026-09-10, check-out 2026-09-14, 4 night\(s\)/m);
  assert.doesNotMatch(walk.prompt, /^2\. [A-Z]{3} — check-in/m);
  assert.doesNotMatch(walk.prompt, /outbound:AV3:J/);
  assert.doesNotMatch(walk.prompt, /USM/);
  assert.match(walk.prompt, new RegExp(`getaway stays ingest my-trip --inputs-fp ${FP}`));
  assert.ok(!labels().includes('stays:ingest-empty'));
});

test('stays: an all-deferred worklist stamps through an empty ingest, no walk', async () => {
  const state = mkState();
  const deferred = WORKLIST.filter((e) => e.disposition === 'deferred');
  const { calls, labels, result } = await runWorkflow(
    ARGS,
    baseScript(roundTripGraph({ lodging: true }), state, {
      'stays:intervals': { inputs_fp: FP, journeys: deferred },
    }),
  );

  assert.ok(!labels().includes('stays:walk'));
  const ingest = findCall(calls, 'stays:ingest-empty');
  assert.match(ingest.prompt, /\{"stays": \{\}\}/);
  assert.match(ingest.prompt, new RegExp(`--inputs-fp ${FP}`));
  assert.deepStrictEqual(result.nodes.stays, { state: 'done' });
});

test('stays: a clamped interval carries its disclosure into the walk prompt', async () => {
  const state = mkState();
  const clamped = [
    {
      ...WORKLIST[0],
      interval: { check_in: '2026-09-10', check_out: '2026-09-17', nights: 5, night_clamped: true },
      search_key: 'OKA|2026-09-10|5',
    },
  ];
  const { calls } = await runWorkflow(
    ARGS,
    baseScript(roundTripGraph({ lodging: true }), state, { 'stays:intervals': { inputs_fp: FP, journeys: clamped } }),
  );

  assert.match(findCall(calls, 'stays:walk').prompt, /clamped to the rooms\.aero 5-night cap; disclose night_clamped: true/);
});

test('stays: a shell-metacharacter destination is rejected, never spliced', async () => {
  const state = mkState();
  const bad = [{ ...WORKLIST[0], destination_airport: 'OKA; rm -rf /' }];
  await runRejects(
    ARGS,
    baseScript(roundTripGraph({ lodging: true }), state, { 'stays:intervals': { inputs_fp: FP, journeys: bad } }),
    /unsafe airport code/,
  );
});

test('stays: a malformed inputs fingerprint is rejected, never spliced', async () => {
  const state = mkState();
  await runRejects(
    ARGS,
    baseScript(roundTripGraph({ lodging: true }), state, { 'stays:intervals': { inputs_fp: 'zzz', journeys: WORKLIST } }),
    /unsafe inputs fingerprint/,
  );
});

// ── Injection guards on the tainted graph boundary ─────────────────────────

test('guard: an unsafe emitted command token is rejected at Load', async () => {
  const state = mkState();
  const graph = oneWayGraph();
  graph.nodes[0].command = ['getaway', 'sweep', 'run', SLUG, 'outbound:x;rm'];
  await runRejects(ARGS, baseScript(graph, state), /unsafe command token/);
});

test('guard: a graph for the wrong slug is rejected', async () => {
  const state = mkState();
  const graph = oneWayGraph({ slug: 'other-trip' });
  await runRejects(ARGS, baseScript(graph, state), /graph for other-trip/);
});

test('guard: a project arg that smuggles uv flags is rejected at validation', async () => {
  const smuggled = ['/tmp/x --with evil-pkg', '/tmp/x\t--with\tevil-pkg', '/tmp/x --index-url evil', 'cli'];
  for (const project of smuggled) {
    await assert.rejects(runWorkflow({ ...ARGS, project }, {}), /project must be an absolute path/);
  }

  const state = mkState();
  const { result } = await runWorkflow({ ...ARGS, project: '/Users/x/Code/getaway/cli' }, baseScript(oneWayGraph(), state));
  assert.strictEqual(result.status, 'complete');
});

test('guard: a node command off its kind allowlist is rejected at Load, nothing dispatched', async () => {
  const offMenu = [
    ['sweep:outbound:asia-beach', ['getaway', 'cache', 'prune', '--older-than', '0h'], /off the sweep allowlist/],
    ['rank', ['getaway', 'expand', 'run', SLUG], /off the rank allowlist/],
    ['sweep:outbound:asia-beach', ['getaway', 'sweep', 'run', SLUG, 'outbound:asia-beach', '--refresh'], /off the sweep allowlist/],
    ['assess', ['getaway', 'cache', 'prune'], /off the assess allowlist/],
  ];
  for (const [id, command, pattern] of offMenu) {
    const graph = oneWayGraph();
    graph.nodes.find((n) => n.id === id).command = command;
    const h = await runRejects(ARGS, baseScript(graph, mkState()), pattern);
    assert.deepStrictEqual(h.labels(), ['load'], `${command.join(' ')} must throw before any dispatch`);
  }
});

test('guard: a command carrying a foreign slug is rejected at Load', async () => {
  const graph = oneWayGraph();
  graph.nodes.find((n) => n.id === 'sweep:outbound:asia-beach').command = ['getaway', 'sweep', 'run', 'victim-trip', 'outbound:asia-beach'];
  const h = await runRejects(ARGS, baseScript(graph, mkState()), /off the sweep allowlist/);
  assert.deepStrictEqual(h.labels(), ['load']);

  const lodged = roundTripGraph({ lodging: true });
  lodged.nodes.find((n) => n.id === 'stays').steps = [
    { name: 'intervals', command: ['getaway', 'stays', 'intervals', SLUG] },
    { name: 'ingest', command: ['getaway', 'stays', 'ingest', 'victim-trip'] },
  ];
  const h2 = await runRejects(ARGS, baseScript(lodged, mkState()), /step ingest command .* off the stays allowlist/);
  assert.deepStrictEqual(h2.labels(), ['load']);
});

// Kind coverage of a live round-trip + lodging + hybrid `trip compile` (captured 2026-07-13),
// plus a program-sweep spec: every emitted shape must clear the hardened allowlist.
test('allowlist regression: every emitted command shape from a hybrid+lodging compile walks to done', async () => {
  const graph = hybridLodgingGraph();
  graph.nodes.splice(1, 0, sweepNode('aeroplan-north-america'));
  graph.nodes.find((n) => n.id === 'shortlist:outbound').inputs.push('legs/outbound/sweep-aeroplan-north-america.json');
  const state = mkState();
  const { result } = await runWorkflow(ARGS, baseScript(graph, state));

  assert.strictEqual(result.status, 'complete');
  for (const [id, s] of Object.entries(result.nodes)) assert.strictEqual(s.state, 'done', `${id} should be done`);
});

// A compile-derived program-sweep label reaches {source ≤32}-from-{continent ≤13} = 51 chars, past
// the old 32-char LEG_SPEC label bound; the widened grammar must clear the full spec end-to-end.
test('allowlist regression: a 51-char program-sweep spec walks to done', async () => {
  const graph = oneWayGraph();
  const label = 'virginatlantic-from-north-america';
  graph.nodes.unshift(sweepNode(label));
  graph.nodes.find((n) => n.id === 'shortlist:outbound').inputs.push(`legs/outbound/sweep-${label}.json`);
  const { result } = await runWorkflow(ARGS, baseScript(graph, mkState()));

  assert.strictEqual(result.status, 'complete');
  assert.strictEqual(result.nodes[`sweep:outbound:${label}`].state, 'done');
});

test('injection regression: every embedded command line stays inside the allowlist', async () => {
  const state = mkState();
  const { calls } = await runWorkflow(
    ARGS,
    baseScript(hybridLodgingGraph(), state, {
      judgmentFactors: ['seat_quality', 'cash_anomaly', 'destination_context', 'transit_risk'],
    }),
  );

  const CMD = /uv run --project (\S+) getaway ([^\n]*)/g;
  const SAFE_CMD = /^[A-Za-z0-9 :._/-]+$/;
  let cmdCount = 0;
  for (const { prompt } of calls) {
    for (const m of prompt.matchAll(CMD)) {
      cmdCount++;
      assert.strictEqual(m[1], PROJECT);
      assert.match(m[2].replace(/<[^>]*>/g, ''), SAFE_CMD);
    }
  }
  assert.ok(cmdCount > 20);
});
