import assert from 'node:assert/strict';
import { test } from 'node:test';

import { runWorkflow } from './harness.mjs';

const PROJECT = '/Users/yasyf/Code/getaway/cli';
const SLUG = 'my-trip';
const ARGS = { project: PROJECT, slug: SLUG };

const ABS_PATH = /^\/[^"`$;|&\n<>()\\]*$/;
const IATA = /^[A-Z]{3}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const SAFE_CMD = /^[A-Za-z0-9 ._/-]+$/;

const candidate = (id, dest, mileage) => ({
  id, date: '2026-09-10', origin: 'SFO', dest, source: 'aeroplan',
  mileage, seats: 2, airlines: 'NH', direct: true, soft: false, departure_day_match: false,
});
const CANDIDATES = [candidate('AV1', 'NRT', 75000), candidate('AV2', 'HND', 80000)];
const GATEWAYS = [candidate('GW1', 'NRT', 60000)];
const MINIMA = [{
  gateway: 'NRT', onward_dest: 'BKK', cabin: 'business', id: 'ON1',
  date: '2026-09-12', source: 'ana', mileage: 30000, seats: 2, airlines: 'TG', direct: true,
}];
const BRIDGE_PAIRS = [{ gateway: 'NRT', onward_dest: 'BKK', cash_cutoff_minutes: 240 }];

const mkExpand = (id) => ({
  id, mileage: 72000, total_taxes: 120, taxes_currency: 'USD', remaining_seats: 2, total_duration: 660,
  segments: [{
    origin: 'SFO', dest: 'NRT', departs_local: '2026-09-10T11:00:00', arrives_local: '2026-09-11T14:00:00',
    flight_number: 'NH7', carrier: 'NH', aircraft: '777-300ER', duration_minutes: 660, cabin: 'J',
  }],
  layovers: [], booking: 'https://book.example/nh7', product: 'solid', product_note: 'solid reverse herringbone',
});

const mkContext = (o = {}) => ({
  sweepLabels: o.sweepLabels ?? [{ label: 'beach', fresh: true }, { label: 'warm', fresh: true }],
  hybrid: o.hybrid ?? false,
  roundTrip: o.roundTrip ?? false,
  activeFactors: o.activeFactors ?? ['layovers'],
  maxFinalists: o.maxFinalists ?? 6,
  party: o.party ?? 2,
  phaseMap: o.phaseMap ?? {},
  quotaRemaining: o.quotaRemaining ?? 500,
  quotaLow: o.quotaLow ?? false,
});

const baseScript = (over = {}) => ({
  load: mkContext(),
  shortlist: { candidates: CANDIDATES, considered: 40 },
  'shortlist:gateway': { candidates: GATEWAYS, considered: 10 },
  onward: { minima: MINIMA, bridge_pairs: BRIDGE_PAIRS, quota_remaining: 400 },
  'sweep:*': (opts) => ({ label: opts.label.slice('sweep:'.length), rows: 100, quota_remaining: 450 }),
  'bridge:*': () => ({
    gateway: 'NRT', onward_dest: 'BKK', cabin: 'business', price: 450, currency: 'USD',
    airline: 'TG', flight_number: 'TG677', stops: 0, duration_minutes: 360,
  }),
  'expand:*': (opts) => mkExpand(opts.label.slice('expand:'.length)),
  'evidence:verify': { verify: [{ id: 'AV1', product: 'solid', note: 'reverse herringbone' }] },
  'evidence:cash': { cash: [{ id: 'AV1', route: 'SFO-NRT', cabin: 'business', quoted: 900, typical: 3000, currency: 'USD', anomaly: true, note: 'below typical' }] },
  'evidence:context': { context: [{ dest: 'NRT', weather: 'mild', visa: 'none', appeal: 'great', events: 'none' }] },
  'evidence:transit': { transit: [{ airport: 'ICN', kind: 'transit', risk: 'none', note: 'airside ok' }] },
  'evidence:return': { return: [{ id: 'AV1', dest: 'NRT', origin: 'SFO', verified: true, rows: 5, note: 'cached' }] },
  assess: { finalists: [{ id: 'AV1', factors: { layovers: { verdict: 'neutral', evidence: 'nonstop' } } }] },
  'persist:*': { ok: true },
  rank: { ranked: 2 },
  finalize: { finalists: 2, hybrids: 1 },
  ...over,
});

test('fresh-skip: every phase fresh dispatches zero sweep agents', async () => {
  const load = mkContext({
    sweepLabels: [{ label: 'beach', fresh: true }, { label: 'warm', fresh: true }],
    phaseMap: { expand: 'fresh', assess: 'fresh' },
  });
  const { result, withPrefix, called } = await runWorkflow(ARGS, baseScript({ load }));

  assert.strictEqual(withPrefix('sweep:').length, 0);
  assert.ok(result.skipped.includes('sweep:beach'));
  assert.ok(result.skipped.includes('sweep:warm'));
  assert.ok(result.skipped.includes('expand'));
  assert.ok(result.skipped.includes('assess'));
  assert.ok(called('finalize'));
  assert.strictEqual(result.slug, SLUG);
  assert.strictEqual(result.finalists, 2);
});

test('per-collector resume: fresh evidence.verify skipped, stale evidence.cash runs', async () => {
  const load = mkContext({
    activeFactors: ['seat_quality', 'cash_anomaly', 'layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale', 'evidence.verify': 'fresh', 'evidence.cash': 'stale' },
  });
  const { result, called } = await runWorkflow(ARGS, baseScript({ load }));

  assert.ok(called('evidence:cash'));
  assert.ok(called('persist:evidence.cash'));
  assert.ok(!called('evidence:verify'));
  assert.ok(!called('persist:evidence.verify'));
  assert.ok(result.skipped.includes('evidence.verify'));
});

test('quota-low: no onward/bridge, sweeps trim to one label, expand caps at maxFinalists', async () => {
  const load = mkContext({
    hybrid: true, quotaLow: true, quotaRemaining: 20, maxFinalists: 2,
    activeFactors: ['seat_quality', 'layovers'],
    sweepLabels: [{ label: 'beach', fresh: false }, { label: 'warm', fresh: false }],
    phaseMap: { expand: 'stale', assess: 'stale' },
  });
  const cands = [candidate('AV1', 'NRT', 75000), candidate('AV2', 'HND', 80000), candidate('AV3', 'ICN', 82000)];
  const { called, withPrefix } = await runWorkflow(ARGS, baseScript({ load, shortlist: { candidates: cands, considered: 60 } }));

  assert.strictEqual(withPrefix('sweep:').length, 1);
  assert.ok(!called('onward'));
  assert.strictEqual(withPrefix('bridge:').length, 0);
  assert.ok(withPrefix('expand:').length <= 2);
});

test('hybrid-absent: zero gateway/onward/bridge agents, both stale labels swept', async () => {
  const load = mkContext({
    hybrid: false, activeFactors: ['layovers'],
    sweepLabels: [{ label: 'beach', fresh: false }, { label: 'warm', fresh: false }],
    phaseMap: { expand: 'stale', assess: 'stale' },
  });
  const { called, withPrefix } = await runWorkflow(ARGS, baseScript({ load }));

  assert.ok(!called('shortlist:gateway'));
  assert.ok(!called('onward'));
  assert.strictEqual(withPrefix('bridge:').length, 0);
  assert.strictEqual(withPrefix('sweep:').length, 2);
});

test('stringified args: the JSON.parse workaround path runs end to end', async () => {
  const load = mkContext({
    activeFactors: ['layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'fresh', assess: 'fresh' },
  });
  const { result } = await runWorkflow(JSON.stringify(ARGS), baseScript({ load }));

  assert.strictEqual(result.slug, SLUG);
  assert.strictEqual(result.finalists, 2);
});

test('injection regression: every embedded command line stays inside the allowlist', async () => {
  const load = mkContext({
    hybrid: true, roundTrip: true,
    activeFactors: ['seat_quality', 'cash_anomaly', 'destination_context', 'transit_risk', 'return_viability', 'layovers'],
    sweepLabels: [{ label: 'beach', fresh: false }, { label: 'gateways', fresh: false }],
    phaseMap: {
      expand: 'stale', assess: 'stale', bridge: 'stale',
      'evidence.verify': 'stale', 'evidence.cash': 'stale', 'evidence.context': 'stale',
      'evidence.transit': 'stale', 'evidence.return': 'stale',
    },
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));

  const CMD = /uv run --project (\S+) getaway ([^\n]*)/g;
  const FLI = /fli flights (\S+) (\S+) (\S+) /g;
  let cmdCount = 0;
  let fliCount = 0;
  for (const { prompt } of calls) {
    for (const m of prompt.matchAll(CMD)) {
      cmdCount++;
      assert.strictEqual(m[1], PROJECT);
      assert.match(m[1], ABS_PATH);
      assert.match(m[2].replace(/<[^>]*>/g, ''), SAFE_CMD);
    }
    for (const m of prompt.matchAll(FLI)) {
      fliCount++;
      assert.match(m[1], IATA);
      assert.match(m[2], IATA);
      assert.match(m[3], DATE);
    }
  }
  assert.ok(cmdCount > 0);
  assert.ok(fliCount > 0);
});

test('injection regression: a shell-metacharacter id is rejected, never spliced', async () => {
  const load = mkContext({
    activeFactors: ['layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale' },
  });
  const shortlist = { candidates: [candidate('AV1; rm -rf /', 'NRT', 75000)], considered: 1 };
  await assert.rejects(runWorkflow(ARGS, baseScript({ load, shortlist })), /unsafe availability id/);
});
