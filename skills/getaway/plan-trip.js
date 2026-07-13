export const meta = {
  name: 'getaway-plan-trip',
  description:
    'Award-trip planning fan-out: parallel seats.aero sweeps, an offline jq shortlist, per-finalist trip expansion, and optional vibe enrichment.',
  whenToUse:
    'Invoked by the getaway skill for planning asks spanning 2+ destination buckets or programs. Args come from stored preferences plus AskUserQuestion answers. Returns the merged finalists; the calling session builds the cc-present board.',
  phases: [
    { title: 'Sweep', detail: 'One agent per destination bucket or program runs a single seats.aero search/availability call into a scratchpad JSONL; a hybrid ask adds one gateway sweep from the origins to the gateway set.' },
    { title: 'Shortlist', detail: 'Offline jq over the sweep files filters, dedups, and ranks the direct finalists, and separately shortlists each gateway\'s best award — zero API calls.' },
    { title: 'Onward', detail: 'Hybrid only: one search from the top gateways to the onward destinations, jq-projected to per-(origin, dest, cabin) award minimums — the two-award-stitch sweep across every program in one call.' },
    { title: 'Bridge', detail: 'Hybrid only: one fli agent per gateway-to-onward pair prices the cash hop — economy first, business when it runs past the cash-cabin cutoff — spending zero seats.aero quota.' },
    { title: 'Expand', detail: 'One agent per direct finalist and per hybrid award leg expands its trip ID into bookable truth at that leg\'s cabin: integer miles, exact taxes, segments, booking link — and on a business leg, classifies the longest business segment against the seat-quality table.' },
    { title: 'Enrich', detail: 'When a vibe is set, one WebSearch agent per direct and onward destination adds weather, visa, and appeal color — no quota spent.' },
  ],
};

const ABS_PATH = /^\/[^"`$;|&\n<>()\\]*$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const IATA = /^[A-Z]{3}$/;
const BUCKET = /^[a-z0-9-]+$/;
const SOURCE = /^[a-z0-9_]+$/;
const CABIN = /^[a-z]+$/;
const AIRLINE = /^[A-Z0-9]{2}$/;
const TRIP_ID = /^[A-Za-z0-9._-]+$/;
const REGIONS = ['North America', 'South America', 'Africa', 'Asia', 'Europe', 'Oceania'];
const CABIN_PREFIX = { economy: 'Y', premium: 'W', business: 'J', first: 'F' };
const PRODUCT = ['suite', 'solid', 'dated', 'barely', 'verify', 'unknown'];

const SWEEP_SCHEMA = {
  type: 'object',
  required: ['rows', 'quota'],
  properties: { rows: { type: 'number' }, quota: { type: 'number' }, note: { type: 'string' } },
};
const SHORTLIST_SCHEMA = {
  type: 'object',
  required: ['rows', 'considered'],
  properties: {
    considered: { type: 'number' },
    rows: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'date', 'origin', 'dest', 'source', 'mileage', 'seats', 'airlines', 'direct', 'soft'],
        properties: {
          id: { type: 'string' }, date: { type: 'string' }, origin: { type: 'string' }, dest: { type: 'string' },
          source: { type: 'string' }, mileage: { type: 'number' }, seats: { type: 'number' },
          airlines: { type: 'string' }, direct: { type: 'boolean' }, soft: { type: 'boolean' },
        },
      },
    },
  },
};
const TRIP_SCHEMA = {
  type: 'object',
  required: ['id', 'quota'],
  properties: {
    id: { type: 'string' }, mileageCost: { type: 'number' }, totalTaxes: { type: 'number' },
    taxesCurrency: { type: 'string' }, remainingSeats: { type: 'number' }, flightNumbers: { type: 'string' },
    segments: { type: 'array', items: { type: 'string' } }, bookingPrimary: { type: 'string' },
    updatedAt: { type: 'string' }, quota: { type: 'number' },
    longhaul: { type: ['string', 'null'] }, product: { enum: PRODUCT }, productNote: { type: 'string' },
  },
};
const VERIFY_SCHEMA = {
  type: 'object',
  required: ['id', 'product', 'productNote'],
  properties: {
    id: { type: 'string' }, product: { enum: PRODUCT.filter(p => p !== 'verify') }, productNote: { type: 'string' },
  },
};
const ENRICH_SCHEMA = {
  type: 'object',
  required: ['dest', 'weather', 'visaNote', 'appeal'],
  properties: { dest: { type: 'string' }, weather: { type: 'string' }, visaNote: { type: 'string' }, appeal: { type: 'string' } },
};
const ONWARD_SCHEMA = {
  type: 'object',
  required: ['rows', 'quota'],
  properties: {
    quota: { type: 'number' },
    rows: {
      type: 'array',
      items: {
        type: 'object',
        required: ['origin', 'dest', 'cabin', 'id', 'date', 'source', 'mileage', 'seats'],
        properties: {
          origin: { type: 'string' }, dest: { type: 'string' }, cabin: { type: 'string' },
          id: { type: 'string' }, date: { type: 'string' }, source: { type: 'string' },
          mileage: { type: 'number' }, seats: { type: 'number' },
          airlines: { type: 'string' }, direct: { type: 'boolean' },
        },
      },
    },
  },
};
const BRIDGE_SCHEMA = {
  type: 'object',
  required: ['gateway', 'dest', 'date', 'cabin', 'price', 'currency'],
  properties: {
    gateway: { type: 'string' }, dest: { type: 'string' }, date: { type: 'string' },
    durationMinutes: { type: 'number' }, cabin: { enum: ['economy', 'business'] },
    price: { type: 'number' }, currency: { type: 'string' },
    airline: { type: 'string' }, flightNumber: { type: 'string' }, stops: { type: 'number' },
  },
};

// Validate every arg up front; the regexes double as the injection guard for strings
// interpolated into the Bash command lines the agents run.
const a = args;
if (typeof a.script !== 'string' || !ABS_PATH.test(a.script)) throw new Error('plan-trip: script must be an absolute path to getaway.sh');
if (typeof a.scratchpad !== 'string' || !ABS_PATH.test(a.scratchpad)) throw new Error('plan-trip: scratchpad must be an absolute path');
if (typeof a.startDate !== 'string' || !DATE.test(a.startDate)) throw new Error('plan-trip: startDate must be YYYY-MM-DD');
if (typeof a.endDate !== 'string' || !DATE.test(a.endDate)) throw new Error('plan-trip: endDate must be YYYY-MM-DD');
if (!Array.isArray(a.origins) || a.origins.length === 0 || !a.origins.every(o => typeof o === 'string' && IATA.test(o))) throw new Error('plan-trip: origins must be a non-empty array of IATA codes');

const buckets = a.buckets ?? [];
if (!Array.isArray(buckets)) throw new Error('plan-trip: buckets must be an array');
for (const b of buckets) {
  if (!b || typeof b.name !== 'string' || !BUCKET.test(b.name)) throw new Error('plan-trip: bucket name must match /^[a-z0-9-]+$/');
  if (!Array.isArray(b.dests) || b.dests.length === 0 || !b.dests.every(d => typeof d === 'string' && IATA.test(d))) throw new Error(`plan-trip: bucket ${b.name} dests must be a non-empty IATA array`);
}

const programSweeps = a.programSweeps ?? [];
if (!Array.isArray(programSweeps)) throw new Error('plan-trip: programSweeps must be an array');
for (const p of programSweeps) {
  if (!p || typeof p.source !== 'string' || !SOURCE.test(p.source)) throw new Error('plan-trip: programSweeps source must match /^[a-z0-9_]+$/');
  if (!REGIONS.includes(p.destRegion)) throw new Error(`plan-trip: programSweeps destRegion must be one of ${REGIONS.join(', ')}`);
}
if (buckets.length + programSweeps.length < 1) throw new Error('plan-trip: need at least one bucket or programSweep');

const cabin = a.cabin ?? 'business';
if (!CABIN.test(cabin) || !CABIN_PREFIX[cabin]) throw new Error('plan-trip: cabin must be one of economy, premium, business, first');
const jc = CABIN_PREFIX[cabin];

if (a.mileageCeiling !== undefined && !(typeof a.mileageCeiling === 'number' && a.mileageCeiling > 0)) throw new Error('plan-trip: mileageCeiling must be a positive number');
const mileageCeiling = a.mileageCeiling;

const avoidDestinations = a.avoidDestinations ?? [];
if (!Array.isArray(avoidDestinations) || !avoidDestinations.every(d => typeof d === 'string' && IATA.test(d))) throw new Error('plan-trip: avoidDestinations must be IATA codes');

const avoidAirlines = a.avoidAirlines ?? [];
if (!Array.isArray(avoidAirlines) || !avoidAirlines.every(x => x && AIRLINE.test(x.code) && (x.strength === 'hard' || x.strength === 'soft'))) throw new Error('plan-trip: avoidAirlines items must be {code, strength: hard|soft}');

const avoidTransit = a.avoidTransit ?? [];
if (!Array.isArray(avoidTransit) || !avoidTransit.every(d => typeof d === 'string' && IATA.test(d))) throw new Error('plan-trip: avoidTransit must be IATA codes');

const sources = a.sources ?? [];
if (!Array.isArray(sources) || !sources.every(s => typeof s === 'string' && SOURCE.test(s))) throw new Error('plan-trip: sources must be program slugs');

const travelers = a.travelers ?? 1;
if (!Number.isInteger(travelers) || travelers < 1) throw new Error('plan-trip: travelers must be a positive integer');

let maxFinalists = a.maxFinalists ?? 6;
if (!Number.isInteger(maxFinalists) || maxFinalists < 1) throw new Error('plan-trip: maxFinalists must be a positive integer');
maxFinalists = Math.min(maxFinalists, 10);
const isBiz = cabin === 'business';
const expandTarget = isBiz ? Math.min(Math.ceil(maxFinalists * 1.5), 12) : maxFinalists;

if (a.vibe !== undefined && (typeof a.vibe !== 'string' || a.vibe.length === 0)) throw new Error('plan-trip: vibe must be a non-empty string');
const vibe = a.vibe;

const quotaFloor = a.quotaFloor ?? 100;
if (!Number.isInteger(quotaFloor)) throw new Error('plan-trip: quotaFloor must be an integer');

// Hybrid routings are opt-in; absent, every hybrid phase is skipped and behavior is unchanged.
// The IATA regex doubles as the injection guard, and an onward destination is a final
// destination — so it must clear avoidDestinations, while gateways stay exempt as waypoints.
const hybrid = a.hybrid;
let cashCutoffMinutes = 240;
let maxHybrids = 3;
if (hybrid !== undefined) {
  if (typeof hybrid !== 'object' || hybrid === null || Array.isArray(hybrid)) throw new Error('plan-trip: hybrid must be an object');
  if (!Array.isArray(hybrid.gateways) || hybrid.gateways.length === 0 || !hybrid.gateways.every(g => typeof g === 'string' && IATA.test(g))) throw new Error('plan-trip: hybrid.gateways must be a non-empty array of IATA codes');
  if (hybrid.onwardDests !== undefined && (!Array.isArray(hybrid.onwardDests) || !hybrid.onwardDests.every(d => typeof d === 'string' && IATA.test(d)))) throw new Error('plan-trip: hybrid.onwardDests must be IATA codes');
  if (hybrid.onwardDests !== undefined && hybrid.onwardDests.some(d => avoidDestinations.includes(d))) throw new Error('plan-trip: hybrid.onwardDests must not intersect avoidDestinations; an onward destination is a final destination');
  cashCutoffMinutes = hybrid.cashCutoffMinutes ?? 240;
  if (!Number.isInteger(cashCutoffMinutes) || cashCutoffMinutes <= 0) throw new Error('plan-trip: hybrid.cashCutoffMinutes must be a positive integer');
  maxHybrids = hybrid.maxHybrids ?? 3;
  if (!Number.isInteger(maxHybrids) || maxHybrids < 1) throw new Error('plan-trip: hybrid.maxHybrids must be a positive integer');
  maxHybrids = Math.min(maxHybrids, 4);
}

const { script, scratchpad, startDate, endDate, origins } = a;
const slug = s => s.toLowerCase().replace(/ /g, '-');

const sweepSpecs = [
  ...buckets.map(b => ({
    label: b.name,
    file: `${scratchpad}/sweep-${b.name}.jsonl`,
    cmd: `"${script}" search --origin ${origins.join(',')} --dest ${b.dests.join(',')} --start ${startDate} --end ${endDate} --cabin ${cabin} --take 1000 --order lowest_mileage`,
  })),
  ...programSweeps.map(p => ({
    label: `${p.source}-${slug(p.destRegion)}`,
    file: `${scratchpad}/sweep-${p.source}-${slug(p.destRegion)}.jsonl`,
    cmd: `"${script}" availability --source ${p.source} --cabin ${cabin} --dest-region "${p.destRegion}" --take 1000 --start ${startDate} --end ${endDate}`,
  })),
  ...(hybrid !== undefined ? [{
    label: 'gateways',
    gateway: true,
    file: `${scratchpad}/sweep-gateways.jsonl`,
    cmd: `"${script}" search --origin ${origins.join(',')} --dest ${hybrid.gateways.join(',')} --start ${startDate} --end ${endDate} --cabin ${cabin} --take 1000 --order lowest_mileage`,
  }] : []),
];
if (new Set(sweepSpecs.map(s => s.file)).size !== sweepSpecs.length) throw new Error('plan-trip: sweep filenames collide; bucket names and program sweeps must be distinct');

phase('Sweep');
const sweepResults = await pipeline(sweepSpecs, spec => agent(
  `Run exactly this one Bash command, nothing else:\n\n${spec.cmd} > "${spec.file}"\n\n` +
  `It writes JSONL rows to that file and prints \`quota remaining: N\` to stderr. Then report:\n` +
  `- rows: the line count (run \`wc -l < "${spec.file}"\`)\n` +
  `- quota: the integer N from the \`quota remaining: N\` stderr line, or -1 if that line is absent.\n` +
  `Run no other getaway.sh command.`,
  { label: `sweep:${spec.label}`, phase: 'Sweep', schema: SWEEP_SCHEMA },
));

// Trust our own paths, never an agent-echoed string, in the shortlist command line.
const okSweeps = sweepResults.filter(Boolean);
const sweepFiles = sweepSpecs.filter((s, i) => sweepResults[i]).map(s => s.file);
// The gateway sweep feeds only the hybrid shortlist; the direct shortlist never sees it.
const directSweepFiles = sweepSpecs.filter((s, i) => sweepResults[i] && !s.gateway).map(s => s.file);
const gatewaySweepFiles = sweepSpecs.filter((s, i) => sweepResults[i] && s.gateway).map(s => s.file);
if (sweepResults.length - okSweeps.length > 0) log(`${sweepResults.length - okSweeps.length} sweep agent(s) died; their files are excluded`);

// The on-disk quota cache is last-writer-wins under a parallel burst, so trust the
// per-agent reports over `getaway.sh quota`.
const quotas = okSweeps.map(r => r.quota).filter(q => q >= 0);
const minQuota = quotas.length ? Math.min(...quotas) : -1;
const quotaLow = minQuota >= 0 && minQuota < quotaFloor;
const enrichRan = Boolean(vibe) && !quotaLow;
// Low quota gates every API-spending hybrid phase — Onward, Bridge, and the hybrid expansions.
const hybridOn = hybrid !== undefined && !quotaLow;
let expandCap = expandTarget;
let expandTrimmedTo = null;
if (quotaLow) {
  expandCap = Math.min(maxFinalists, 3, minQuota);
  expandTrimmedTo = expandCap;
  log(`quota low (${minQuota} < floor ${quotaFloor}): trimming Expand to ${expandCap} finalists and skipping Enrich and hybrids`);
}
const skipped = { enrich: !enrichRan, expandTrimmedTo, hybrids: hybrid !== undefined && quotaLow };

if (sweepFiles.length === 0) {
  log('no sweep files produced; returning empty finalists');
  return { finalists: [], hybrids: [], sweepFiles, considered: 0, quota: minQuota, skipped };
}

const avail = `.${jc}Available`;
const mileageF = `(.${jc}MileageCost|tonumber)`;
const seatsF = `(.${jc}RemainingSeats|tonumber)`;
const airlinesF = `.${jc}Airlines`;
const directF = `.${jc}Direct`;
const hard = JSON.stringify(avoidAirlines.filter(x => x.strength === 'hard').map(x => x.code));
const soft = JSON.stringify(avoidAirlines.filter(x => x.strength === 'soft').map(x => x.code));
const baseHead = [
  `${avail} == true`,
  ...(mileageCeiling !== undefined ? [`${mileageF} <= ${mileageCeiling}`] : []),
  `${seatsF} >= ${travelers}`,
  `(.Route.OriginAirport as $o | (${JSON.stringify(origins)} | index($o)) != null)`,
  ...(sources.length ? [`(.Source as $s | (${JSON.stringify(sources)} | index($s)) != null)`] : []),
];
// The avoid-destinations veto is a direct-shortlist clause only — a gateway is a waypoint, never an endpoint.
const avoidDestClause = `(.Route.DestinationAirport as $d | (${JSON.stringify(avoidDestinations)} | index($d)) | not)`;
const airlinesHardClause = `(${airlinesF} | split(", ") | all(. as $c | ${hard} | index($c) == null))`;
const clauses = [...baseHead, avoidDestClause, airlinesHardClause].join('\n        and ');
const gatewayClauses = [...baseHead, airlinesHardClause].join('\n        and ');
const rowProj =
  `{ id: .ID, date: .Date, origin: .Route.OriginAirport, dest: .Route.DestinationAirport,\n` +
  `                source: .Source, mileage: ${mileageF}, seats: ${seatsF},\n` +
  `                airlines: ${airlinesF}, direct: ${directF},\n` +
  `                soft: (${airlinesF} | split(", ") | any(. as $c | ${soft} | index($c) != null)) }`;
const shortlistJq =
  `jq -s '{ considered: length,\n` +
  `  rows: ( [ .[] | select(${clauses})\n` +
  `            | ${rowProj} ]\n` +
  `          | group_by([.origin, .dest, .date, .source]) | map(sort_by(.soft, .mileage) | .[0])\n` +
  `          | sort_by(.soft, .mileage) | .[0:${expandTarget}] ) }' ` +
  directSweepFiles.map(f => `"${f}"`).join(' ');
const gatewayShortlistJq =
  `jq -s '{ considered: length,\n` +
  `  rows: ( [ .[] | select(${gatewayClauses})\n` +
  `            | ${rowProj} ]\n` +
  `          | group_by(.dest) | map(sort_by(.soft, .mileage) | .[0])\n` +
  `          | sort_by(.soft, .mileage) | .[0:3] ) }' ` +
  gatewaySweepFiles.map(f => `"${f}"`).join(' ');

phase('Shortlist');
const shortlist = await agent(
  `Run exactly this one Bash command — a single offline jq pass over the sweep files, zero API calls, run no getaway.sh command:\n\n${shortlistJq}\n\n` +
  `Return its JSON output: considered (total rows scanned) and rows (the ranked finalists, each with id, date, origin, dest, source, mileage, seats, airlines, direct, soft). mileage and seats are numbers; direct and soft are booleans.`,
  { label: 'shortlist', phase: 'Shortlist', schema: SHORTLIST_SCHEMA },
);
if (!shortlist || shortlist.rows.length === 0) {
  log('shortlist produced 0 rows; no finalists');
  return { finalists: [], hybrids: [], sweepFiles, considered: shortlist?.considered ?? 0, quota: minQuota, skipped };
}

// Gateway shortlist: each gateway's best award, deduped to one row per hub, at most 3 distinct gateways.
let gatewayRows = [];
if (hybridOn && gatewaySweepFiles.length) {
  const gatewayShortlist = await agent(
    `Run exactly this one Bash command — a single offline jq pass over the gateway sweep file, zero API calls, run no getaway.sh command:\n\n${gatewayShortlistJq}\n\n` +
    `Return its JSON output: considered (total rows scanned) and rows (each gateway's best award, each with id, date, origin, dest, source, mileage, seats, airlines, direct, soft). mileage and seats are numbers; direct and soft are booleans.`,
    { label: 'shortlist:gateways', phase: 'Shortlist', schema: SHORTLIST_SCHEMA },
  );
  if (gatewayShortlist) gatewayRows = gatewayShortlist.rows;
}

const rows = shortlist.rows.slice(0, expandCap);
for (const r of rows) if (!TRIP_ID.test(r.id)) throw new Error(`plan-trip: shortlist row id failed the injection guard: ${r.id}`);

// Re-check seats and connections against the bookable truth: the cached row's cheapest
// live trip can seat fewer travelers or connect through an avoided airport.
const transitClause = avoidTransit.length
  ? ` and ([.AvailabilitySegments[1:][].OriginAirport] | all(. as $x | (${JSON.stringify(avoidTransit)} | index($x)) == null))`
  : '';

// Derive the seat-quality doc from the validated script path, never an agent-echoed string.
const seatDoc = script.replace(/[^/]+$/, 'seat-quality.md');
const longhaulProjFor = c =>
  `,\n        longhaul: ((.AvailabilitySegments | map(select(.Cabin == "${c}")) | max_by(.Distance)) as $s\n` +
  `          | if $s == null then null else "\\($s.FlightNumber) \\($s.OriginAirport)-\\($s.DestinationAirport) (\\($s.AircraftName))" end)`;

// Onward, Bridge, Compose: build hybrid routings — a gateway award plus a cash hop (gateway-cash)
// or a stitched onward award (two-award). Every award leg is expanded below with the directs, so
// composition only decides which leg ids and cabins to expand.
const onwardDests = hybridOn ? (hybrid.onwardDests ?? [...new Set(shortlist.rows.map(r => r.dest))].slice(0, 4)) : [];
const gatewayRowByDest = {};
for (const r of gatewayRows) {
  if (!TRIP_ID.test(r.id)) throw new Error(`plan-trip: gateway row id failed the injection guard: ${r.id}`);
  if (!DATE.test(r.date)) throw new Error(`plan-trip: gateway row date failed the injection guard: ${r.date}`);
  gatewayRowByDest[r.dest] = r;
}
const topGateways = Object.keys(gatewayRowByDest);
for (const g of topGateways) if (!IATA.test(g)) throw new Error(`plan-trip: gateway dest failed the injection guard: ${g}`);
for (const d of onwardDests) if (!IATA.test(d)) throw new Error(`plan-trip: onward dest failed the injection guard: ${d}`);

let composed = [];
if (hybridOn && topGateways.length && onwardDests.length) {
  const onwardBlocks = ['economy', 'business'].map(cab => {
    const p = CABIN_PREFIX[cab];
    return `[ .[] | select(.${p}Available == true and (.${p}RemainingSeats|tonumber) >= ${travelers})\n` +
      `        | { origin: .Route.OriginAirport, dest: .Route.DestinationAirport, cabin: "${cab}",\n` +
      `            id: .ID, date: .Date, source: .Source, mileage: (.${p}MileageCost|tonumber),\n` +
      `            seats: (.${p}RemainingSeats|tonumber), airlines: .${p}Airlines, direct: .${p}Direct } ]`;
  }).join('\n      + ');
  const onwardFile = `${scratchpad}/onward.jsonl`;
  const onwardSearchCmd = `"${script}" search --origin ${topGateways.join(',')} --dest ${onwardDests.join(',')} --start ${startDate} --end ${endDate} --take 1000 --order lowest_mileage`;
  const onwardJq =
    `jq -s '{ rows: ( ${onwardBlocks}\n` +
    `      | group_by([.origin, .dest, .cabin]) | map(min_by(.mileage)) ) }' "${onwardFile}"`;

  phase('Onward');
  const onward = await agent(
    `Run exactly these two Bash commands in order, nothing else — the search spends one seats.aero call, the jq is offline; run no other getaway.sh command:\n\n` +
    `${onwardSearchCmd} > "${onwardFile}"\n\n${onwardJq}\n\n` +
    `The search prints \`quota remaining: N\` to stderr. Return:\n` +
    `- rows: the jq output's rows (each with origin, dest, cabin, id, date, source, mileage, seats, airlines, direct; mileage and seats are numbers, direct a boolean)\n` +
    `- quota: the integer N from the \`quota remaining: N\` stderr line, or -1 if that line is absent.`,
    { label: 'onward', phase: 'Onward', schema: ONWARD_SCHEMA },
  );
  const onwardRows = onward ? onward.rows : [];
  for (const r of onwardRows) if (!TRIP_ID.test(r.id)) throw new Error(`plan-trip: onward row id failed the injection guard: ${r.id}`);
  if (onward && onward.quota >= 0) quotas.push(onward.quota);

  const bridgePairs = [];
  for (const g of topGateways) for (const d of onwardDests) if (d !== g) bridgePairs.push({ gateway: g, dest: d, date: gatewayRowByDest[g].date });
  const cappedBridgePairs = bridgePairs.slice(0, 8);

  phase('Bridge');
  const bridgeQuotes = await pipeline(cappedBridgePairs, pair => agent(
    `Price the ${pair.gateway}-${pair.dest} cash hop on ${pair.date} with fli via uvx — zero seats.aero quota, run no getaway.sh command. Economy first:\n\n` +
    `uvx --from "flights[mcp]" fli flights ${pair.gateway} ${pair.dest} ${pair.date} --class ECONOMY --format json | jq '{cheapest: (.flights | min_by(.price) | {price, currency, stops, duration, airline: .legs[0].airline.code, flight: .legs[0].flight_number})}'\n\n` +
    `If cheapest.duration exceeds ${cashCutoffMinutes} minutes, re-quote business — the same command with \`--class BUSINESS\` — and report that quote instead; otherwise report the economy quote. Return:\n` +
    `- gateway: "${pair.gateway}"\n- dest: "${pair.dest}"\n- date: "${pair.date}"\n` +
    `- durationMinutes: the reported cheapest.duration (a number)\n` +
    `- cabin: "economy" when you kept the economy quote, "business" when you re-quoted\n` +
    `- price: cheapest.price (a number)\n- currency: cheapest.currency\n- stops: cheapest.stops (a number)\n` +
    `- airline: cheapest.airline\n- flightNumber: cheapest.flight`,
    { label: `bridge:${pair.gateway}-${pair.dest}`, phase: 'Bridge', schema: BRIDGE_SCHEMA },
  ));
  const bridgeByPair = {};
  for (const q of bridgeQuotes.filter(Boolean)) bridgeByPair[`${q.gateway}|${q.dest}`] = q;
  const onwardByKey = {};
  for (const r of onwardRows) onwardByKey[`${r.origin}|${r.dest}|${r.cabin}`] = r;

  for (const pair of cappedBridgePairs) {
    const bridge = bridgeByPair[`${pair.gateway}|${pair.dest}`];
    if (!bridge) continue;
    const award = gatewayRowByDest[pair.gateway];
    composed.push({
      kind: 'gateway-cash', gateway: pair.gateway, dest: pair.dest, award,
      onward: { mode: 'cash', cabin: bridge.cabin, price: bridge.price, currency: bridge.currency,
                durationMinutes: bridge.durationMinutes, stops: bridge.stops,
                airline: bridge.airline, flightNumber: bridge.flightNumber, date: bridge.date },
    });
    // Two-award stitch: the onward award at the cutoff-picked cabin, departing on/after the gateway award.
    const onwardRow = onwardByKey[`${pair.gateway}|${pair.dest}|${bridge.cabin}`];
    if (onwardRow && onwardRow.date >= award.date) {
      composed.push({
        kind: 'two-award', gateway: pair.gateway, dest: pair.dest, award,
        onward: { mode: 'award', cabin: bridge.cabin, id: onwardRow.id, date: onwardRow.date,
                  source: onwardRow.source, mileage: onwardRow.mileage, seats: onwardRow.seats,
                  airlines: onwardRow.airlines, direct: onwardRow.direct },
      });
    }
  }
  // Rank by total miles then cash; the cash leg carries no miles. Keep the cheapest maxHybrids.
  const totalMiles = h => h.award.mileage + (h.onward.mode === 'award' ? h.onward.mileage : 0);
  const cashMinor = h => (h.onward.mode === 'cash' ? Math.round(h.onward.price * 100) : 0);
  composed.sort((a, b) => (totalMiles(a) - totalMiles(b)) || (cashMinor(a) - cashMinor(b)));
  composed = composed.slice(0, maxHybrids);
}

// Every award leg expands at its own cabin: directs and gateway legs at the trip cabin,
// stitched onward legs at the cabin the cutoff picked.
const directItems = rows.map(r => ({ id: r.id, cabin, label: r.dest }));
const hybridItems = [];
const seenItem = new Set();
for (const h of composed) {
  const gKey = `${h.award.id}|${cabin}`;
  if (!seenItem.has(gKey)) { seenItem.add(gKey); hybridItems.push({ id: h.award.id, cabin, label: h.gateway }); }
  if (h.onward.mode === 'award') {
    const oKey = `${h.onward.id}|${h.onward.cabin}`;
    if (!seenItem.has(oKey)) { seenItem.add(oKey); hybridItems.push({ id: h.onward.id, cabin: h.onward.cabin, label: h.dest }); }
  }
}
const expandItems = [...directItems, ...hybridItems];

phase('Expand');
const expandResults = await pipeline(expandItems, item => agent(
  (item.cabin === 'business'
    ? `Run exactly this one Bash pipeline, then one local file read — no other getaway.sh command:\n\n`
    : `Run exactly this one Bash pipeline, nothing else:\n\n`) +
  `"${script}" trip ${item.id} | jq '{ bookingPrimary: ([.booking_links[] | select(.primary) | .label][0]),\n` +
  `  best: ((.data | map(select(.Cabin == "${item.cabin}" and .RemainingSeats >= ${travelers}${transitClause})) | min_by(.MileageCost)) as $t\n` +
  `    | if $t == null then null else $t | { MileageCost, TotalTaxes, TaxesCurrency, RemainingSeats, FlightNumbers, UpdatedAt,\n` +
  `        segments: [.AvailabilitySegments[] | "\\(.FlightNumber) \\(.OriginAirport)-\\(.DestinationAirport) \\(.Cabin) (\\(.AircraftName))"]${item.cabin === 'business' ? longhaulProjFor(item.cabin) : ''} } end) }'\n\n` +
  `It also prints \`quota remaining: N\` to stderr. If \`best\` is null — no trip in that cabin seats ` +
  `${travelers} traveler(s) and clears the connection rules — return only id and quota. Otherwise return:\n` +
  `- id: "${item.id}"\n- mileageCost: best.MileageCost\n- totalTaxes: best.TotalTaxes\n- taxesCurrency: best.TaxesCurrency\n` +
  `- remainingSeats: best.RemainingSeats\n- flightNumbers: best.FlightNumbers\n- segments: best.segments\n` +
  `- updatedAt: best.UpdatedAt\n- bookingPrimary: bookingPrimary\n- quota: the integer N from the stderr line, or -1 if absent.` +
  (item.cabin === 'business'
    ? `\n\nThen read the local file ${seatDoc} and classify \`best.longhaul\`, the longest business segment: take the operating carrier from its flight-number prefix plus its aircraft, match that carrier+aircraft against the table, and also return — all three required whenever best is non-null:\n` +
      `- longhaul: best.longhaul (the segment string, or null)\n` +
      `- product: that row's Verdict — but \`verify\` when the row is Verify-marked, and \`unknown\` when the carrier+aircraft is absent from the table or best.longhaul is null\n` +
      `- productNote: the product name plus one clause (e.g. "old Club World — yin-yang 2-3-2, barely business")`
    : ''),
  { label: `trip:${item.label}`, phase: 'Expand', schema: TRIP_SCHEMA },
));
const trips = expandResults.slice(0, rows.length);
const tripByKey = {};
expandItems.forEach((it, i) => { if (expandResults[i]) tripByKey[`${it.id}|${it.cabin}`] = expandResults[i]; });

// Business only, zero quota: resolve Verify-marked and table-absent products against the
// live seat map before the re-rank truncates, so a resolved `barely` never displaces a true flat.
const verdictById = {};
if (isBiz) {
  const toVerify = rows.map((row, i) => ({ row, trip: trips[i] }))
    .filter(c => c.trip && (c.trip.product === 'verify' || (c.trip.product === 'unknown' && c.trip.longhaul)));
  if (toVerify.length) {
    phase('Verify');
    const verified = await pipeline(toVerify, ({ row, trip }) => agent(
      `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to pin down the business hard product on ${trip.longhaul} departing ${row.date}: the carrier's seat map for that flight number and date, recent cabin reviews, retrofit trackers. Return:\n` +
      `- id: "${row.id}"\n` +
      `- product: the resolved verdict — one of suite, solid, dated, barely, unknown; return \`unknown\` when sources disagree\n` +
      `- productNote: the product name plus one clause describing the hard product.`,
      { label: `verify:${row.dest}`, phase: 'Verify', schema: VERIFY_SCHEMA },
    ));
    // Merge by row.id, never index — verify runs over a subset of the finalists.
    for (let i = 0; i < toVerify.length; i++) if (verified[i]) verdictById[toVerify[i].row.id] = verified[i];
  }
}

const candidates = rows.map((row, i) => ({ row, trip: trips[i] })).filter(c => c.trip && typeof c.trip.mileageCost === 'number');
for (const c of candidates) {
  const v = verdictById[c.row.id];
  if (v) { c.trip.product = v.product; c.trip.productNote = v.productNote; }
}
// Soft-avoided airlines sink harder than bad seats; only literal `barely` demotes.
const demoted = c => (c.trip.product === 'barely' ? 1 : 0);
candidates.sort((a, b) =>
  (Number(a.row.soft) - Number(b.row.soft)) || (demoted(a) - demoted(b)) || (a.row.mileage - b.row.mileage));
const kept = candidates.slice(0, maxFinalists);
const died = rows.length - candidates.length;
if (died > 0) log(`${died} finalist(s) dropped: expansion agent died or no live trip seats the party`);

// Attach each hybrid leg's bookable truth; drop a hybrid whose award leg did not expand to a seatable trip.
const finalHybrids = composed
  .map(h => {
    const awardTrip = tripByKey[`${h.award.id}|${cabin}`];
    const onwardTrip = h.onward.mode === 'award' ? tripByKey[`${h.onward.id}|${h.onward.cabin}`] : null;
    return { ...h, award: { ...h.award, trip: awardTrip }, onward: onwardTrip ? { ...h.onward, trip: onwardTrip } : { ...h.onward } };
  })
  .filter(h => h.award.trip && typeof h.award.trip.mileageCost === 'number'
    && (h.onward.mode !== 'award' || (h.onward.trip && typeof h.onward.trip.mileageCost === 'number')));
const hybridsDied = composed.length - finalHybrids.length;
if (hybridsDied > 0) log(`${hybridsDied} hybrid(s) dropped: an award leg did not expand to a seatable trip`);

const enrichByDest = {};
if (enrichRan) {
  phase('Enrich');
  const dests = [...new Set([...kept.map(c => c.row.dest), ...finalHybrids.map(h => h.dest)])];
  const enriched = await pipeline(dests, dest => agent(
    `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to research airport ${dest} for travel in ${startDate.slice(0, 7)}. Return:\n` +
    `- dest: "${dest}"\n- weather: the typical weather and season for that window\n` +
    `- visaNote: a short entry/visa note for a US passport holder\n- appeal: one or two sentences on how it fits a "${vibe}" trip.`,
    { label: `enrich:${dest}`, phase: 'Enrich', schema: ENRICH_SCHEMA },
  ));
  for (const e of enriched.filter(Boolean)) enrichByDest[e.dest] = e;
}

const finalists = kept.map(({ row, trip }) => {
  const enrich = enrichByDest[row.dest];
  return enrich ? { ...row, trip, enrich } : { ...row, trip };
});
const hybrids = finalHybrids.map(h => {
  const enrich = enrichByDest[h.dest];
  return enrich ? { ...h, enrich } : h;
});

const allQuotas = [...quotas, ...expandResults.filter(Boolean).map(t => t.quota).filter(q => q >= 0)];
return { finalists, hybrids, sweepFiles, considered: shortlist.considered, quota: allQuotas.length ? Math.min(...allQuotas) : -1, skipped };
