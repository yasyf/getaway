export const meta = {
  name: 'plan-trip',
  description:
    'Award-trip planning fan-out over the frozen getaway CLI: quota-aware seats.aero sweeps, offline SQL shortlists, optional hybrid gateway/onward/bridge stitching, per-finalist expansion, zero-quota evidence collectors, and a judgment-shaped assess/rank/finalize tail. State lives on disk under ~/.getaway; phase checkpoints let a killed run resume without re-spending quota.',
  phases: [
    { title: 'Load', detail: 'One agent reads trip status and the quota floor into a CONTEXT the workflow branches on.' },
    { title: 'Sweep', detail: 'One agent per stale sweep label spends one seats.aero call; low quota trims to the first bucket label.' },
    { title: 'Shortlist', detail: 'Offline SQL shortlist of direct finalists, plus a gateway shortlist on hybrid asks.' },
    { title: 'Onward', detail: 'Hybrid and quota permitting: the onward sweep plus offline onward minima and bridge pairs.' },
    { title: 'Bridge', detail: 'Hybrid and quota permitting: one fli cash-pricing agent per bridge pair, zero seats.aero quota.' },
    { title: 'Expand', detail: 'One agent per unique finalist and hybrid leg id expands bookable truth and classifies business product.' },
    { title: 'Evidence', detail: 'Heterogeneous parallel: one zero-quota collector per active judgment factor whose evidence phase is stale.' },
    { title: 'Assess', detail: 'One agent turns artifacts and guidance into per-finalist per-factor verdicts with evidence lines.' },
    { title: 'Rank', detail: 'The CLI applies deterministic facts and judgment tiers within mileage bands.' },
    { title: 'Finalize', detail: 'The CLI merges directs and composes hybrids into the final finalists artifact.' },
  ],
};

// papercut 6d2e0ad: the harness sometimes hands args JSON-stringified; reparse (user-approved workaround).
if (typeof args === 'string') args = JSON.parse(args);

// The regexes double as the injection guard for every value spliced into an agent command line.
const ABS_PATH = /^\/[^"`$;|&\n<>()\\]*$/;
const SLUG = /^[a-z0-9][a-z0-9-]{1,63}$/;
const LABEL = /^[a-z0-9][a-z0-9_-]*$/;
const FACTOR = /^[a-z_]+$/;
const AVAIL_ID = /^[A-Za-z0-9._-]+$/;
const IATA = /^[A-Z]{3}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// Active judgment factor -> Evidence-phase collector (mirrors cli/getaway/constants.py EVIDENCE_COLLECTORS).
const COLLECTOR_OF = {
  seat_quality: 'verify',
  cash_anomaly: 'cash',
  destination_context: 'context',
  transit_risk: 'transit',
  return_viability: 'return',
};

const CONTEXT_SCHEMA = {
  type: 'object',
  required: ['sweepLabels', 'hybrid', 'roundTrip', 'activeFactors', 'maxFinalists', 'party', 'phaseMap', 'quotaRemaining', 'quotaLow'],
  properties: {
    sweepLabels: {
      type: 'array',
      items: { type: 'object', required: ['label', 'fresh'], properties: { label: { type: 'string' }, fresh: { type: 'boolean' } } },
    },
    hybrid: { type: 'boolean' },
    roundTrip: { type: 'boolean' },
    activeFactors: { type: 'array', items: { type: 'string' } },
    maxFinalists: { type: 'number' },
    party: { type: 'number' },
    phaseMap: { type: 'object' },
    quotaRemaining: { type: ['number', 'null'] },
    quotaLow: { type: 'boolean' },
  },
};
const SWEEP_SCHEMA = {
  type: 'object',
  required: ['label', 'rows'],
  properties: { label: { type: 'string' }, rows: { type: 'number' }, quota_remaining: { type: ['number', 'null'] }, skipped: { type: 'boolean' } },
};
const CANDIDATE = {
  type: 'object',
  required: ['id', 'date', 'origin', 'dest', 'source', 'mileage', 'seats', 'airlines', 'direct'],
  properties: {
    id: { type: 'string' }, date: { type: 'string' }, origin: { type: 'string' }, dest: { type: 'string' },
    source: { type: 'string' }, mileage: { type: 'number' }, seats: { type: ['number', 'null'] },
    airlines: { type: 'string' }, direct: { type: 'boolean' }, soft: { type: 'boolean' }, departure_day_match: { type: 'boolean' },
  },
};
const SHORTLIST_SCHEMA = {
  type: 'object',
  required: ['candidates', 'considered'],
  properties: { candidates: { type: 'array', items: CANDIDATE }, considered: { type: 'number' } },
};
const ONWARD_SCHEMA = {
  type: 'object',
  required: ['minima', 'bridge_pairs'],
  properties: {
    quota_remaining: { type: ['number', 'null'] },
    minima: {
      type: 'array',
      items: {
        type: 'object',
        required: ['gateway', 'onward_dest', 'cabin', 'id', 'date', 'source', 'mileage'],
        properties: {
          gateway: { type: 'string' }, onward_dest: { type: 'string' }, cabin: { type: 'string' }, id: { type: 'string' },
          date: { type: 'string' }, source: { type: 'string' }, mileage: { type: 'number' }, seats: { type: ['number', 'null'] },
          airlines: { type: 'string' }, direct: { type: 'boolean' },
        },
      },
    },
    bridge_pairs: {
      type: 'array',
      items: {
        type: 'object',
        required: ['gateway', 'onward_dest', 'cash_cutoff_minutes'],
        properties: { gateway: { type: 'string' }, onward_dest: { type: 'string' }, cash_cutoff_minutes: { type: 'number' } },
      },
    },
  },
};
const BRIDGE_SCHEMA = {
  type: 'object',
  required: ['gateway', 'onward_dest', 'cabin', 'price', 'currency', 'stops', 'duration_minutes'],
  properties: {
    gateway: { type: 'string' }, onward_dest: { type: 'string' }, cabin: { enum: ['economy', 'business'] },
    price: { type: 'number' }, currency: { type: 'string' }, airline: { type: 'string' },
    flight_number: { type: 'string' }, stops: { type: 'number' }, duration_minutes: { type: 'number' },
  },
};
const EXPAND_SCHEMA = {
  type: 'object',
  required: ['id', 'mileage', 'segments', 'layovers'],
  properties: {
    id: { type: 'string' }, mileage: { type: 'number' }, total_taxes: { type: ['number', 'null'] },
    taxes_currency: { type: ['string', 'null'] }, remaining_seats: { type: ['number', 'null'] },
    total_duration: { type: ['number', 'null'] }, segments: { type: 'array' }, layovers: { type: 'array' },
    booking: { type: ['string', 'null'] }, product: { type: ['string', 'null'] }, product_note: { type: ['string', 'null'] },
  },
};
const VERIFY_SCHEMA = {
  type: 'object',
  required: ['verify'],
  properties: { verify: { type: 'array', items: { type: 'object', required: ['id', 'product'], properties: { id: { type: 'string' }, product: { type: 'string' }, note: { type: 'string' } } } } },
};
const CASH_SCHEMA = {
  type: 'object',
  required: ['cash'],
  properties: { cash: { type: 'array', items: { type: 'object', required: ['id', 'anomaly'], properties: { id: { type: 'string' }, route: { type: 'string' }, cabin: { type: 'string' }, quoted: { type: ['number', 'null'] }, typical: { type: ['number', 'null'] }, currency: { type: 'string' }, anomaly: { type: 'boolean' }, note: { type: 'string' } } } } },
};
const CONTEXT_EV_SCHEMA = {
  type: 'object',
  required: ['context'],
  properties: { context: { type: 'array', items: { type: 'object', required: ['dest', 'weather', 'appeal'], properties: { dest: { type: 'string' }, weather: { type: 'string' }, visa: { type: 'string' }, appeal: { type: 'string' }, events: { type: 'string' } } } } },
};
const TRANSIT_SCHEMA = {
  type: 'object',
  required: ['transit'],
  properties: { transit: { type: 'array', items: { type: 'object', required: ['airport', 'kind', 'risk'], properties: { airport: { type: 'string' }, kind: { enum: ['transit', 'entry'] }, risk: { enum: ['none', 'possible', 'required'] }, note: { type: 'string' } } } } },
};
const RETURN_SCHEMA = {
  type: 'object',
  required: ['return'],
  properties: { return: { type: 'array', items: { type: 'object', required: ['id', 'verified'], properties: { id: { type: 'string' }, dest: { type: 'string' }, origin: { type: 'string' }, verified: { type: 'boolean' }, rows: { type: 'number' }, note: { type: 'string' } } } } },
};
const ASSESS_SCHEMA = {
  type: 'object',
  required: ['finalists'],
  properties: { finalists: { type: 'array', items: { type: 'object', required: ['id', 'factors'], properties: { id: { type: 'string' }, factors: { type: 'object' } } } } },
};
const RANK_SCHEMA = { type: 'object', required: ['ranked'], properties: { ranked: { type: 'number' } } };
const FINALIZE_SCHEMA = { type: 'object', required: ['finalists', 'hybrids'], properties: { finalists: { type: 'number' }, hybrids: { type: 'number' } } };
const PERSIST_SCHEMA = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' } } };

// Validate the four args off the wire; everything else comes from disk via the CLI.
if (typeof args.project !== 'string' || !ABS_PATH.test(args.project)) throw new Error('plan-trip: project must be an absolute path to the cli/ dir');
if (typeof args.slug !== 'string' || !SLUG.test(args.slug)) throw new Error('plan-trip: slug must match ^[a-z0-9][a-z0-9-]{1,63}$');
const refresh = args.refresh === undefined ? false : args.refresh;
if (typeof refresh !== 'boolean') throw new Error('plan-trip: refresh must be a boolean');
const quotaFloor = args.quotaFloor === undefined ? 100 : args.quotaFloor;
if (!Number.isInteger(quotaFloor)) throw new Error('plan-trip: quotaFloor must be an integer');

const project = args.project;
const slug = args.slug;
const CLI = `uv run --project ${project} getaway`;

// Every value below is re-validated with its regex const immediately before it is spliced into a prompt.
const gLabel = (v) => { if (!LABEL.test(v)) throw new Error(`plan-trip: unsafe sweep label ${v}`); return v; };
const gFactor = (v) => { if (!FACTOR.test(v)) throw new Error(`plan-trip: unsafe factor/collector id ${v}`); return v; };
const gId = (v) => { if (typeof v !== 'string' || !AVAIL_ID.test(v)) throw new Error(`plan-trip: unsafe availability id ${v}`); return v; };
const gIata = (v) => { if (!IATA.test(v)) throw new Error(`plan-trip: unsafe airport code ${v}`); return v; };
const gDate = (v) => { if (!DATE.test(v)) throw new Error(`plan-trip: unsafe date ${v}`); return v; };
const uniq = (xs) => [...new Set(xs)];

const skipped = [];
const stale = (key) => refresh || context.phaseMap[key] !== 'fresh';

let quotaRemaining = null;
let quotaLow = false;
const foldQuota = (q) => {
  if (typeof q === 'number' && q >= 0) {
    quotaRemaining = quotaRemaining === null ? q : Math.min(quotaRemaining, q);
    if (quotaRemaining < quotaFloor) quotaLow = true;
  }
};

const persist = (name, phaseKey, phaseTitle, json, deps) =>
  agent(
    `Persist an artifact, then stamp the phase complete — run no other getaway command.\n` +
    `First, run this command exactly, feeding the JSON below on standard input verbatim:\n` +
    `${CLI} trip artifact write ${slug} ${name}\n` +
    `stdin:\n${json}\n` +
    `Then run this command exactly:\n` +
    `${CLI} trip phase-done ${slug} ${phaseKey}${deps.map((d) => ` --artifact ${d}`).join('')}\n` +
    `Return {"ok": true} once both commands exit 0.`,
    { label: `persist:${phaseKey}`, phase: phaseTitle, schema: PERSIST_SCHEMA },
  );

// ── Phase 0: Load ──────────────────────────────────────────────────────────
phase('Load');
const context = await agent(
  `Run these two commands and shape their output into one CONTEXT object — run no other getaway command.\n` +
  `1. ${CLI} trip status ${slug}\n` +
  `   Emits JSON with keys: slug, sweep_labels, hybrid, round_trip, active_factors, max_finalists, party, phase_map, quota.\n` +
  `2. ${CLI} quota check --floor ${quotaFloor}\n` +
  `   Exits 0 when quota is at or above the floor, 1 when it is below, 4 when no quota has been recorded yet.\n\n` +
  `Return CONTEXT:\n` +
  `- sweepLabels: for every label in sweep_labels EXCEPT the reserved "onward" label, {label, fresh: phase_map["sweep:"+label] === "fresh"}\n` +
  `- hybrid: true when status.hybrid is a non-null object, else false\n` +
  `- roundTrip: status.round_trip\n` +
  `- activeFactors: status.active_factors\n` +
  `- maxFinalists: status.max_finalists\n` +
  `- party: status.party\n` +
  `- phaseMap: status.phase_map verbatim\n` +
  `- quotaRemaining: status.quota is null ? null : status.quota.remaining\n` +
  `- quotaLow: true ONLY when command 2 exited 1; false on exit 0 or exit 4.`,
  { label: 'load', phase: 'Load', schema: CONTEXT_SCHEMA },
);
quotaRemaining = context.quotaRemaining;
quotaLow = context.quotaLow;
const activeFactors = context.activeFactors.map(gFactor);
const hybrid = context.hybrid;
const maxFinalists = context.maxFinalists;
const classifyProduct = activeFactors.includes('seat_quality');
// Active judgment factors that own an Evidence collector; their evidence files exist by Assess time.
const activeCollectors = uniq(activeFactors.map((f) => COLLECTOR_OF[f]).filter(Boolean));

// ── Phase 1: Sweep ─────────────────────────────────────────────────────────
const allLabels = context.sweepLabels.map((s) => s.label);
let sweepTargets = (refresh ? allLabels : context.sweepLabels.filter((s) => !s.fresh).map((s) => s.label));
for (const s of context.sweepLabels) if (s.fresh && !refresh) skipped.push(`sweep:${s.label}`);
if (quotaLow) sweepTargets = sweepTargets.slice(0, 1); // quota-low: only the first (bucket) label
if (sweepTargets.length) {
  phase('Sweep');
  const flag = refresh ? ' --refresh' : '';
  const sweeps = await pipeline(sweepTargets, (label) =>
    agent(
      `Run exactly this one command, then report its JSON — run no other getaway command:\n` +
      `${CLI} sweep run ${slug} ${gLabel(label)}${flag}\n` +
      `It ingests one seats.aero call into the cache and writes the sweep artifact. Return label, rows, and quota_remaining from its JSON (quota_remaining may be null when no header was seen).`,
      { label: `sweep:${label}`, phase: 'Sweep', schema: SWEEP_SCHEMA },
    ),
  );
  for (const s of sweeps) foldQuota(s.quota_remaining);
}

// ── Phase 2: Shortlist ─────────────────────────────────────────────────────
phase('Shortlist');
const shortlist = await agent(
  `Run exactly this one offline command (zero seats.aero quota) and return its JSON — run no other getaway command:\n` +
  `${CLI} shortlist run ${slug}\n` +
  `Return candidates (each with id, date, origin, dest, source, mileage, seats, airlines, direct, soft, departure_day_match) and considered.`,
  { label: 'shortlist', phase: 'Shortlist', schema: SHORTLIST_SCHEMA },
);
const candidates = shortlist.candidates;
for (const c of candidates) gId(c.id);

let gatewayCandidates = [];
let ranGateway = false;
if (hybrid) {
  ranGateway = true;
  const gatewayDoc = await agent(
    `Run exactly this one offline command (zero seats.aero quota) and return its JSON — run no other getaway command:\n` +
    `${CLI} shortlist run ${slug} --gateway\n` +
    `Return candidates (each gateway's best award: id, date, origin, dest, source, mileage, seats, airlines, direct, soft, departure_day_match) and considered.`,
    { label: 'shortlist:gateway', phase: 'Shortlist', schema: SHORTLIST_SCHEMA },
  );
  gatewayCandidates = gatewayDoc.candidates;
  for (const c of gatewayCandidates) { gId(c.id); gIata(c.dest); }
}

// ── Phase 3: Onward ────────────────────────────────────────────────────────
let onwardMinima = [];
let bridgePairs = [];
let ranOnward = false;
if (hybrid && !quotaLow) {
  ranOnward = true;
  phase('Onward');
  const onward = await agent(
    `Run exactly these two commands in order, then return their combined JSON — run no other getaway command.\n` +
    `1. ${CLI} sweep run ${slug} onward\n` +
    `   The onward sweep; it self-skips (spending zero quota) when still fresh, otherwise spends one seats.aero call.\n` +
    `2. ${CLI} shortlist onward ${slug}\n` +
    `   Offline onward minima and bridge pairs.\n\n` +
    `Return minima and bridge_pairs from command 2's JSON, plus quota_remaining from command 1's JSON (null when it self-skipped or no header was seen).`,
    { label: 'onward', phase: 'Onward', schema: ONWARD_SCHEMA },
  );
  onwardMinima = onward.minima;
  bridgePairs = onward.bridge_pairs;
  for (const m of onwardMinima) { gId(m.id); gIata(m.gateway); gIata(m.onward_dest); }
  for (const p of bridgePairs) { gIata(p.gateway); gIata(p.onward_dest); }
  foldQuota(onward.quota_remaining); // onward may have spent a call; re-gate before Bridge and hybrid expansion
}

// ── Phase 4: Bridge ────────────────────────────────────────────────────────
if (hybrid && !quotaLow && bridgePairs.length && stale('bridge')) {
  // Bridge pairs carry no date; source one from the onward minima, falling back to the gateway award's date.
  const dateByPair = {};
  for (const m of onwardMinima) {
    const k = `${m.gateway}|${m.onward_dest}`;
    if (!(k in dateByPair) || m.date < dateByPair[k]) dateByPair[k] = m.date;
  }
  const dateByHub = {};
  for (const c of gatewayCandidates) if (!(c.dest in dateByHub)) dateByHub[c.dest] = c.date;
  const priced = bridgePairs.map((p) => ({
    gateway: gIata(p.gateway),
    dest: gIata(p.onward_dest),
    cutoff: p.cash_cutoff_minutes,
    date: gDate(dateByPair[`${p.gateway}|${p.onward_dest}`] ?? dateByHub[p.gateway]),
  }));
  phase('Bridge');
  const quotes = await pipeline(priced, (p) =>
    agent(
      `Price the ${p.gateway}-${p.dest} cash hop on ${p.date} with fli — zero seats.aero quota, run no getaway command. Economy first:\n\n` +
      `uvx --from "flights[mcp]" fli flights ${p.gateway} ${p.dest} ${p.date} --class ECONOMY --format json\n\n` +
      `From that JSON take the cheapest flight whose price is non-null. If its duration exceeds ${p.cutoff} minutes, re-quote business — the same command with --class BUSINESS — and report that quote instead; otherwise report the economy quote. Return:\n` +
      `- gateway: "${p.gateway}"\n- onward_dest: "${p.dest}"\n- cabin: "economy" when you kept the economy quote, "business" when you re-quoted\n` +
      `- price: the number\n- currency: the code\n- airline: the operating carrier code of the first leg\n- flight_number: the first leg's number\n- stops: the number of stops\n- duration_minutes: the reported cheapest duration in minutes.`,
      { label: `bridge:${p.gateway}-${p.dest}`, phase: 'Bridge', schema: BRIDGE_SCHEMA },
    ),
  );
  await persist('bridge.json', 'bridge', 'Bridge', JSON.stringify({ quotes }), ['onward.json']);
} else if (hybrid && !quotaLow && bridgePairs.length) {
  skipped.push('bridge');
}

// ── Phase 5: Expand ────────────────────────────────────────────────────────
let expandIds = uniq([
  ...candidates.map((c) => c.id),
  ...gatewayCandidates.map((c) => c.id),
  ...onwardMinima.map((m) => m.id),
]).map(gId);
if (quotaLow) expandIds = expandIds.slice(0, maxFinalists); // quota-low caps expansion at the finalists ceiling
if (expandIds.length && stale('expand')) {
  phase('Expand');
  const records = await pipeline(expandIds, (id) =>
    agent(
      `Expand one availability id into bookable truth — run only the commands named here.\n` +
      `Run: ${CLI} expand ${gId(id)}\n` +
      `It returns id, mileage (bookable integer miles), total_taxes, taxes_currency, remaining_seats, total_duration, segments (each with origin, dest, departs_local, arrives_local, flight_number, carrier, aircraft, duration_minutes, cabin as a Y/W/J/F letter), layovers (minutes), and booking_links.\n` +
      (classifyProduct
        ? `Then classify the hard product: pick the longest-duration segment whose cabin letter is "J" (business); if one exists, run\n` +
          `${CLI} quality classify --airline <that segment's carrier> --aircraft <that segment's aircraft>\n` +
          `and set product to its verdict and product_note to its product name plus its note; leave product "verify" when the verdict is verify and "unknown" when no business segment exists.\n`
        : ``) +
      `Return: id "${id}", mileage, total_taxes, taxes_currency, remaining_seats, total_duration, segments, layovers, booking (the primary booking link's url, else the first link's url)` +
      (classifyProduct ? `, product, product_note.` : `.`),
      { label: `expand:${id}`, phase: 'Expand', schema: EXPAND_SCHEMA },
    ),
  );
  const expandMap = {};
  for (const r of records) expandMap[gId(r.id)] = r;
  const deps = ['shortlist.json', ...(ranGateway ? ['shortlist-gateway.json'] : []), ...(ranOnward ? ['onward.json'] : [])];
  await persist('expand.json', 'expand', 'Expand', JSON.stringify(expandMap), deps);
} else if (expandIds.length) {
  skipped.push('expand');
}

// ── Phase 6: Evidence ──────────────────────────────────────────────────────
// One zero-quota collector per active judgment factor whose evidence.<collector> phase is stale.
const COLLECTORS = {
  verify: {
    schema: VERIFY_SCHEMA,
    prompt:
      `Verify the business hard product of every finalist and hybrid leg — WebSearch only, zero seats.aero quota, run only the getaway commands named here.\n` +
      `Read the expanded legs:\n${CLI} trip artifact read ${slug} expand.json\n` +
      `For each record whose product is "verify" or "unknown" and that has a business ("J") segment, confirm the operating carrier and aircraft with the command below, then WebSearch the carrier's seat map for that flight and date, recent cabin reviews, and retrofit trackers to pin the hard product:\n${CLI} quality classify --airline <carrier> --aircraft <aircraft>\n` +
      `Return verify: an array of {id, product (one of suite, solid, dated, barely, unknown — "unknown" when sources disagree), note (the product name plus one clause on the hard product)}.`,
  },
  cash: {
    schema: CASH_SCHEMA,
    prompt:
      `Flag cash-fare anomalies for the business finalists — WebSearch or fli only, zero seats.aero quota, run only the getaway command named here.\n` +
      `Read the finalist routes and mileage: ${CLI} trip artifact read ${slug} expand.json\n` +
      `For each finalist route, compare the current one-way business cash fare against what is typical for that route and season; a fare far below typical for business ("unusually cheap for J") is the signal.\n` +
      `Return cash: an array of {id, route (origin-dest), cabin, quoted (number or null), typical (number or null), currency, anomaly (true when unusually cheap for the cabin), note}.`,
  },
  context: {
    schema: CONTEXT_EV_SCHEMA,
    prompt:
      `Add destination context for each finalist endpoint — WebSearch only, zero seats.aero quota, run only the getaway command named here.\n` +
      `Read the finalist destinations: ${CLI} trip artifact read ${slug} shortlist.json\n` +
      `For each unique dest, research the trip window: typical weather and season, a short entry/visa note, how the place fits the trip vibe, and any notable EVENTS in the window.\n` +
      `Return context: an array of {dest, weather, visa, appeal, events}.`,
  },
  transit: {
    schema: TRANSIT_SCHEMA,
    prompt:
      `Flag transit-visa and entry risk against the traveler's documents — WebSearch only, zero seats.aero quota, run only the getaway commands named here.\n` +
      `Read the traveler documents (passports, residency, standing visas):\n${CLI} prefs show\n` +
      `Read the expanded legs — the origin of every segment after the first is a same-ticket connection airport:\n${CLI} trip artifact read ${slug} expand.json\n` +
      `Read the hybrid gateways — each candidate's dest is a separate-ticket landside self-transfer, an entry rather than airside transit:\n${CLI} trip artifact read ${slug} shortlist-gateway.json\n` +
      `For each unique connection airport determine transit (airside) visa risk; for each gateway determine entry risk. Prefer official government and airport sources.\n` +
      `Return transit: an array of {airport, kind ("transit" or "entry"), risk ("none", "possible", or "required"), note}.`,
  },
  return: {
    schema: RETURN_SCHEMA,
    prompt:
      `Check return-direction award viability for each finalist — zero API calls, cache only, run only the getaway commands named here.\n` +
      `Read the finalists: ${CLI} trip artifact read ${slug} shortlist.json\n` +
      `For each finalist query the cache for return-direction space (dest -> origin): ${CLI} cache query --origin <dest> --dest <origin>\n` +
      `A finalist whose return direction has no cached rows is unverified — flag it rather than spending quota.\n` +
      `Return return: an array of {id, dest, origin, verified (true when cached return rows exist), rows (count), note}.`,
  },
};
const staleCollectors = activeCollectors.filter((c) => {
  if (stale(`evidence.${c}`)) return true;
  skipped.push(`evidence.${c}`);
  return false;
});
if (candidates.length && staleCollectors.length) {
  phase('Evidence');
  await parallel(
    staleCollectors.map((c) => async () => {
      const spec = COLLECTORS[c];
      const evidence = await agent(spec.prompt, { label: `evidence:${gFactor(c)}`, phase: 'Evidence', schema: spec.schema });
      await persist(`evidence-${gFactor(c)}.json`, `evidence.${c}`, 'Evidence', JSON.stringify(evidence), ['shortlist.json']);
    }),
  );
}

// ── Phase 7: Assess ────────────────────────────────────────────────────────
if (candidates.length && stale('assess')) {
  phase('Assess');
  const evidenceReads = activeCollectors
    .map((c) => `- ${CLI} trip artifact read ${slug} evidence-${gFactor(c)}.json`)
    .join('\n');
  const assessment = await agent(
    `Weigh every finalist against the trip's judgment factors and return per-finalist per-factor verdicts — zero seats.aero quota, run only the getaway commands named here.\n` +
    `Read the trip's guidance (use judgment.guidance and judgment.factors):\n${CLI} trip show ${slug}\n` +
    `Read the finalists:\n${CLI} trip artifact read ${slug} shortlist.json\n` +
    `Read the expanded legs:\n${CLI} trip artifact read ${slug} expand.json\n` +
    (evidenceReads ? `Read the collected evidence:\n${evidenceReads}\n` : ``) +
    `\nScore each of these judgment factors for each finalist: ${activeFactors.join(', ') || 'none'}.\n` +
    `The layovers factor follows the layover doctrine: mileage dominates and a verdict only reorders options within a mileage band, so judge each option's layovers on their own merits. Under the traveler's comfortable-connection floor is risky-short (demote and name the margin). Floor to ~3h is comfortable (neutral). ~3-6h is dead time (mild demote, softened at airports that pass hours well like DOH, SIN, ICN). Over ~6h forks on style and city: an explore-style traveler with a city worth leaving the airport for is a promote; a minimize-style traveler, an avoid-listed city, or no feasible exit is a harder demote. Overnight gaps are dead-long unless explore-style and the city warrants it. A nonstop is neutral — never demote for having no layover.\n` +
    `Every other factor gets its verdict from the evidence and guidance you read.\n` +
    `Return finalists: an array of {id, factors} where factors maps each judged factor id to {verdict ("promote", "neutral", or "demote"), evidence (one sentence)}.`,
    { label: 'assess', phase: 'Assess', schema: ASSESS_SCHEMA },
  );
  const assessMap = {};
  for (const f of assessment.finalists) assessMap[gId(f.id)] = f.factors;
  const assessDeps = ['shortlist.json', 'expand.json', ...activeCollectors.map((c) => `evidence-${gFactor(c)}.json`)];
  await persist('assess.json', 'assess', 'Assess', JSON.stringify(assessMap), assessDeps);
} else if (candidates.length) {
  skipped.push('assess');
}

// ── Phase 8: Rank ──────────────────────────────────────────────────────────
phase('Rank');
await agent(
  `Run exactly this one offline command (zero seats.aero quota) and return its result count — run no other getaway command:\n` +
  `${CLI} rank ${slug}\n` +
  `It applies deterministic facts and judgment tiers within mileage bands and writes rank.json. Return {"ranked": <number of ranked entries>}.`,
  { label: 'rank', phase: 'Rank', schema: RANK_SCHEMA },
);

// ── Phase 9: Finalize ──────────────────────────────────────────────────────
phase('Finalize');
const finalized = await agent(
  `Run exactly this one offline command (zero seats.aero quota) and return its counts — run no other getaway command:\n` +
  `${CLI} trip finalize ${slug}\n` +
  `It merges directs and composes hybrids into finalists.json. Return {"finalists": <directs length>, "hybrids": <hybrids length>}.`,
  { label: 'finalize', phase: 'Finalize', schema: FINALIZE_SCHEMA },
);

log(`plan-trip ${slug}: ${finalized.finalists} finalist(s), ${finalized.hybrids} hybrid(s), quota ${quotaRemaining ?? 'unknown'}`);
return { slug, finalists: finalized.finalists, hybrids: finalized.hybrids, quota: quotaRemaining, skipped };
