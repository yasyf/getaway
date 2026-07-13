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
  cabin: o.cabin ?? 'J',
  returnStart: o.returnStart ?? '2026-09-17',
  returnEnd: o.returnEnd ?? '2026-09-24',
});

const baseScript = (over = {}) => {
  const load = over.load ?? mkContext();
  return {
    load,
    shortlist: { candidates: CANDIDATES, considered: 40 },
    'shortlist:gateway': { candidates: GATEWAYS, considered: 10 },
    onward: { minima: MINIMA, bridge_pairs: BRIDGE_PAIRS, quota_remaining: 400 },
    'sweep:*': (opts) => ({ label: opts.label.slice('sweep:'.length), rows: 100, quota_remaining: 450 }),
    // After a sweep that actually ran, the workflow re-reads the phase map; default to the Load
    // snapshot so a plain sweep re-run leaves downstream skip decisions unchanged.
    'status:refresh': { phaseMap: load.phaseMap },
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
    finalize: { finalists: 2, hybrids: 1, quota_remaining: 300 },
    ...over,
  };
};

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

const findCall = (calls, label) => calls.find((c) => c.opts.label === label);

// #1 stale-phase-map-snapshot: a sweep that actually ran must re-read the phase map so downstream
// phases the fresh sweep invalidated stop skipping as "fresh".
test('post-sweep refresh: a sweep that ran re-reads the phase map and re-stales downstream', async () => {
  const load = mkContext({
    sweepLabels: [{ label: 'beach', fresh: false }],
    phaseMap: { expand: 'fresh', assess: 'fresh' },
  });
  const { result, called } = await runWorkflow(ARGS, baseScript({
    load,
    'sweep:*': (opts) => ({ label: opts.label.slice('sweep:'.length), rows: 100, quota_remaining: 450, skipped: false }),
    'status:refresh': { phaseMap: { expand: 'stale', assess: 'stale' } },
  }));

  assert.ok(called('status:refresh'));
  assert.ok(called('expand:AV1'));
  assert.ok(called('expand:AV2'));
  assert.ok(!result.skipped.includes('expand'));
  assert.ok(!result.skipped.includes('assess'));
});

// #1 the other arm: when every dispatched sweep self-skipped, nothing new was ingested — keep the
// Load snapshot and skip the extra trip-status call.
test('post-sweep refresh: every sweep self-skipping keeps the Load snapshot and skips the refresh call', async () => {
  const load = mkContext({
    sweepLabels: [{ label: 'beach', fresh: false }],
    phaseMap: { expand: 'fresh', assess: 'fresh' },
  });
  const { result, called } = await runWorkflow(ARGS, baseScript({
    load,
    'sweep:*': (opts) => ({ label: opts.label.slice('sweep:'.length), rows: 0, quota_remaining: null, skipped: true }),
  }));

  assert.ok(!called('status:refresh'));
  assert.ok(!called('expand:AV1'));
  assert.ok(result.skipped.includes('expand'));
  assert.ok(result.skipped.includes('assess'));
});

// #2 refresh-onward-missing: --refresh must reach the onward sweep, not just the bucket sweeps.
test('refresh: --refresh reaches both bucket sweeps and the onward sweep', async () => {
  const load = mkContext({
    hybrid: true,
    sweepLabels: [{ label: 'beach', fresh: true }, { label: 'warm', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale' },
  });
  const { calls } = await runWorkflow({ ...ARGS, refresh: true }, baseScript({ load }));

  const buckets = calls.filter((c) => c.opts.label.startsWith('sweep:'));
  assert.ok(buckets.length >= 1);
  for (const c of buckets) assert.match(c.prompt, /sweep run my-trip \S+ --refresh/);
  assert.match(findCall(calls, 'onward').prompt, /sweep run my-trip onward --refresh/);
});

// #3 assess-missing-layover-prefs: the assessor must read the traveler's layover preferences.
test('assess: the assessor reads the traveler layover preferences alongside trip guidance', async () => {
  const load = mkContext({
    activeFactors: ['layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale' },
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));
  const assess = findCall(calls, 'assess');
  assert.match(assess.prompt, /getaway prefs show/);
  assert.match(assess.prompt, /layovers\.style/);
  assert.match(assess.prompt, /min_connection_minutes/);
});

// #4 return-cache-underconstrained: the return query must reverse the route and constrain window,
// cabin, party seats, and freshness — and an empty result flags, never filters.
test('return-viability: the cache query carries reversed route, return window, cabin, party, freshness', async () => {
  const load = mkContext({
    activeFactors: ['return_viability', 'layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale', 'evidence.return': 'stale' },
    cabin: 'J', party: 3, returnStart: '2026-09-17', returnEnd: '2026-09-24',
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));
  const ret = findCall(calls, 'evidence:return');
  assert.match(
    ret.prompt,
    /cache query --origin <dest> --dest <origin> --date-start 2026-09-17 --date-end 2026-09-24 --cabin J --min-seats 3 --fresh-within 24h/,
  );
  assert.match(ret.prompt, /never drop it/);
});

// #5 expansion-quota-unreported: the reported quota comes from the Finalize agent's live quota read,
// not the folded sweep/onward headers (which never saw expansion spend).
test('finalize: the workflow reports the final recorded quota from the Finalize agent', async () => {
  const load = mkContext({
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'fresh', assess: 'fresh' },
    quotaRemaining: 500,
  });
  const { result, calls } = await runWorkflow(ARGS, baseScript({
    load,
    finalize: { finalists: 2, hybrids: 1, quota_remaining: 137 },
  }));
  assert.match(findCall(calls, 'finalize').prompt, /getaway quota\n/);
  assert.strictEqual(result.quota, 137);
});

// #6 booking-link-key: the persisted booking link keys off the API "link" field, not "url".
test('expand: the booking contract keys off the API link field, not url', async () => {
  const load = mkContext({
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale' },
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));
  const expand = calls.find((c) => c.opts.label.startsWith('expand:'));
  assert.match(expand.prompt, /primary booking link's link/);
  assert.doesNotMatch(expand.prompt, /booking link's url/);
});

// #7 transit-nonhybrid-artifact: the transit collector must not read the gateway artifact (which
// does not exist) for a non-hybrid trip, and must read it for a hybrid one.
test('transit-nonhybrid: the transit collector omits the gateway artifact read for non-hybrid trips', async () => {
  const load = mkContext({
    hybrid: false,
    activeFactors: ['transit_risk', 'layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale', 'evidence.transit': 'stale' },
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));
  const transit = findCall(calls, 'evidence:transit');
  assert.doesNotMatch(transit.prompt, /shortlist-gateway\.json/);
  assert.doesNotMatch(transit.prompt, /for each gateway determine entry risk/);
});

test('transit-hybrid: the transit collector reads the gateway artifact for hybrid trips', async () => {
  const load = mkContext({
    hybrid: true,
    activeFactors: ['transit_risk', 'layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale', 'evidence.transit': 'stale' },
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));
  const transit = findCall(calls, 'evidence:transit');
  assert.match(transit.prompt, /shortlist-gateway\.json/);
  assert.match(transit.prompt, /for each gateway determine entry risk/);
});

// #8 expand --cabin (cross-lane): every expand command passes the trip cabin letter and the record
// contract carries bookable seats for that cabin.
test('expand: every expand command passes the trip cabin letter and records bookable seats', async () => {
  const load = mkContext({
    hybrid: true,
    activeFactors: ['layovers'],
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale' },
    cabin: 'J',
  });
  const { calls } = await runWorkflow(ARGS, baseScript({ load }));
  const expands = calls.filter((c) => c.opts.label.startsWith('expand:'));
  assert.ok(expands.length > 0);
  for (const c of expands) {
    assert.match(c.prompt, /getaway expand \S+ --cabin J\b/);
    assert.match(c.prompt, /seats \(remaining_seats when/);
  }
});

// #8 the cabin value obeys the interpolation allowlist: a cabin that is not a Y/W/J/F letter is
// rejected before any command line is built.
test('cabin guard: a non Y/W/J/F cabin is rejected before any command line is built', async () => {
  const load = mkContext({
    sweepLabels: [{ label: 'beach', fresh: true }],
    phaseMap: { expand: 'stale', assess: 'stale' },
    cabin: 'business',
  });
  await assert.rejects(runWorkflow(ARGS, baseScript({ load })), /unsafe cabin business/);
});
