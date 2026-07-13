export const meta = {
  name: 'getaway-plan-trip',
  description:
    'Award-trip planning fan-out: parallel seats.aero sweeps, an offline jq shortlist, per-finalist trip expansion, optional vibe enrichment, and transit-visa flags for connections and self-transfer gateways.',
  whenToUse:
    'Invoked by the getaway skill for planning asks spanning 2+ destination buckets or programs. Args come from stored preferences plus AskUserQuestion answers. Returns the merged finalists; the calling session builds the cc-present board.',
  phases: [
    { title: 'Sweep', detail: 'One agent per destination bucket or program runs a single seats.aero search/availability call into a scratchpad JSONL; a hybrid ask adds one gateway sweep from the origins to the gateway set.' },
    { title: 'Shortlist', detail: 'Offline jq over the sweep files filters, dedups, and ranks the direct finalists, and separately shortlists each gateway\'s best award — zero API calls.' },
    { title: 'Onward', detail: 'Hybrid only: one search from the top gateways to the onward destinations, jq-projected to per-(origin, dest, cabin) award minimums — the two-award-stitch sweep across every program in one call.' },
    { title: 'Bridge', detail: 'Hybrid only: one fli agent per gateway-to-onward pair prices the cash hop — economy first, business when it runs past the cash-cabin cutoff — spending zero seats.aero quota.' },
    { title: 'Expand', detail: 'One agent per direct finalist and per hybrid award leg expands its trip ID into bookable truth at that leg\'s cabin: integer miles, exact taxes, segments, booking link — and on a business leg, classifies the longest business segment against the seat-quality table.' },
    { title: 'Verify', detail: 'Business only: one WebSearch agent per Verify-marked or table-absent business leg — direct finalist or hybrid award leg — pins the hard product against the live seat map before the re-rank truncates.' },
    { title: 'Transit', detail: 'Documents only: one WebSearch agent per unique same-ticket connection airport and per hybrid gateway (a separate-ticket self-transfer, so an entry check) flags transit-visa or entry risk against the traveler\'s documents — no quota spent.' },
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
const DOC = /^[A-Za-z0-9][A-Za-z0-9 ()./+-]*$/;
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
const TRANSIT_SCHEMA = {
  type: 'object',
  required: ['airport', 'risk', 'transitNote'],
  properties: { airport: { type: 'string' }, risk: { enum: ['none', 'possible', 'required'] }, transitNote: { type: 'string' } },
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
  required: ['gateway', 'dest', 'date', 'durationMinutes', 'cabin', 'price', 'currency', 'airline', 'flightNumber', 'stops'],
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

// All-empty documents preserve the shipped US-centric phrasing and skip the Transit pass.
const documents = a.documents === undefined ? { passports: [], residency: [], visas: [] } : a.documents;
if (typeof documents !== 'object' || documents === null || Array.isArray(documents)) throw new Error('plan-trip: documents must be an object');
const passports = documents.passports === undefined ? [] : documents.passports;
const residency = documents.residency === undefined ? [] : documents.residency;
const visas = documents.visas === undefined ? [] : documents.visas;
for (const [key, list] of [['passports', passports], ['residency', residency], ['visas', visas]]) {
  if (!Array.isArray(list) || !list.every(x => typeof x === 'string' && DOC.test(x))) throw new Error(`plan-trip: documents.${key} must be an array of safe strings`);
}
const travelerParts = [];
if (passports.length) travelerParts.push(`passport(s): ${passports.join(', ')}`);
if (residency.length) travelerParts.push(`residency: ${residency.join(', ')}`);
if (visas.length) travelerParts.push(`standing visa(s): ${visas.join(', ')}`);
const hasDocuments = travelerParts.length > 0;
const traveler = hasDocuments ? `a traveler holding ${travelerParts.join('; ')}` : 'a US passport holder';

const { script, scratchpad, startDate, endDate, origins } = a;
const slug = s => s.toLowerCase().replace(/ /g, '-');
// Long-haul awards land the next calendar day; string YYYY-MM-DD arithmetic, no wall clock.
const nextDay = d => new Date(new Date(d).getTime() + 86400000).toISOString().slice(0, 10);

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
const skipped = { enrich: !enrichRan, transit: true, expandTrimmedTo, hybrids: hybrid !== undefined && quotaLow };

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
  log('shortlist produced 0 rows; no direct finalists');
  // Hybrids can outlive an empty direct shortlist only with explicit onwardDests; the default
  // onward set derives from the direct finalists, so it too is empty and there's nothing to stitch.
  const canHybrid = Boolean(shortlist) && hybridOn && gatewaySweepFiles.length > 0 && hybrid.onwardDests !== undefined;
  if (!canHybrid) {
    if (hybridOn && gatewaySweepFiles.length > 0 && hybrid.onwardDests === undefined) {
      log('skipping hybrids: onward destinations default from the direct shortlist, which is empty');
      skipped.hybrids = true;
    }
    return { finalists: [], hybrids: [], sweepFiles, considered: shortlist?.considered ?? 0, quota: minQuota, skipped };
  }
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
  // Onward award rows carry the same restrictions as the direct shortlist: the trip's sources (when
  // set) and hard-avoided airlines. Soft avoids stay ranking-only and never touch hybrids.
  const onwardBlocks = ['economy', 'business'].map(cab => {
    const p = CABIN_PREFIX[cab];
    const onwardSel = [
      `.${p}Available == true`,
      `(.${p}RemainingSeats|tonumber) >= ${travelers}`,
      ...(sources.length ? [`(.Source as $s | (${JSON.stringify(sources)} | index($s)) != null)`] : []),
      `(.${p}Airlines | split(", ") | all(. as $c | ${hard} | index($c) == null))`,
    ].join(' and ');
    return `[ .[] | select(${onwardSel})\n` +
      `        | { origin: .Route.OriginAirport, dest: .Route.DestinationAirport, cabin: "${cab}",\n` +
      `            id: .ID, date: .Date, source: .Source, mileage: (.${p}MileageCost|tonumber),\n` +
      `            seats: (.${p}RemainingSeats|tonumber), airlines: .${p}Airlines, direct: .${p}Direct } ]`;
  }).join('\n      + ');
  const onwardFile = `${scratchpad}/onward.jsonl`;
  const onwardSearchCmd = `"${script}" search --origin ${topGateways.join(',')} --dest ${onwardDests.join(',')} --start ${startDate} --end ${endDate} --take 1000 --order lowest_mileage`;
  // Per-(origin, dest, cabin, date) minima so a cheap-but-too-early award can't mask a feasible later one.
  const onwardJq =
    `jq -s '{ rows: ( ${onwardBlocks}\n` +
    `      | group_by([.origin, .dest, .cabin, .date]) | map(min_by(.mileage)) ) }' "${onwardFile}"`;

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

  // Onward spent a seats.aero call; re-check quota before the Bridge fan-out and the hybrid
  // expansions it feeds, mirroring the pre-Shortlist quotaLow gate.
  const postOnwardQuota = quotas.length ? Math.min(...quotas) : -1;
  if (postOnwardQuota >= 0 && postOnwardQuota < quotaFloor) {
    log(`quota low after Onward (${postOnwardQuota} < floor ${quotaFloor}): skipping Bridge and hybrid expansions`);
    skipped.hybrids = true;
  } else {
    // Each cash hop and stitched onward award departs on/after minOnwardDate — the day after the
    // gateway award lands.
    const bridgePairs = [];
    for (const g of topGateways) {
      const minOnwardDate = nextDay(gatewayRowByDest[g].date);
      for (const d of onwardDests) if (d !== g) bridgePairs.push({ gateway: g, dest: d, date: minOnwardDate });
    }
    const cappedBridgePairs = bridgePairs.slice(0, 8);
    if (bridgePairs.length > cappedBridgePairs.length) log(`${bridgePairs.length - cappedBridgePairs.length} gateway×onward pair(s) dropped by the 8-pair cap`);

    phase('Bridge');
    const bridgeQuotes = await pipeline(cappedBridgePairs, pair => agent(
      `Price the ${pair.gateway}-${pair.dest} cash hop on ${pair.date} with fli via uvx — zero seats.aero quota, run no getaway.sh command. Economy first:\n\n` +
      `uvx --from "flights[mcp]" fli flights ${pair.gateway} ${pair.dest} ${pair.date} --class ECONOMY --format json | jq '{cheapest: (.flights | map(select(.price != null)) | min_by(.price) | {price, currency, stops, duration, airline: .legs[0].airline.code, flight: .legs[0].flight_number})}'\n\n` +
      `If cheapest.duration exceeds ${cashCutoffMinutes} minutes, re-quote business — the same command with \`--class BUSINESS\` — and report that quote instead; otherwise report the economy quote. Return:\n` +
      `- gateway: "${pair.gateway}"\n- dest: "${pair.dest}"\n- date: "${pair.date}"\n` +
      `- durationMinutes: the reported cheapest.duration (a number)\n` +
      `- cabin: "economy" when you kept the economy quote, "business" when you re-quoted\n` +
      `- price: cheapest.price (a number)\n- currency: cheapest.currency\n- stops: cheapest.stops (a number)\n` +
      `- airline: cheapest.airline\n- flightNumber: cheapest.flight`,
      { label: `bridge:${pair.gateway}-${pair.dest}`, phase: 'Bridge', schema: BRIDGE_SCHEMA },
    ));
    // Drop cash hops flown by a hard-avoided airline; soft avoids stay ranking-only for hybrids.
    const hardCodes = avoidAirlines.filter(x => x.strength === 'hard').map(x => x.code);
    const bridgeByPair = {};
    for (const q of bridgeQuotes.filter(Boolean)) {
      if (hardCodes.includes(q.airline)) continue;
      bridgeByPair[`${q.gateway}|${q.dest}`] = q;
    }
    // Onward rows are per-(origin, dest, cabin, date); bucket by key, filter to feasible dates below.
    const onwardByKey = {};
    for (const r of onwardRows) {
      const k = `${r.origin}|${r.dest}|${r.cabin}`;
      if (!onwardByKey[k]) onwardByKey[k] = [];
      onwardByKey[k].push(r);
    }

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
      // Two-award stitch: the cheapest onward award at the cutoff-picked cabin departing on/after
      // minOnwardDate (pair.date) — a cheap-but-too-early award never wins over a feasible later one.
      const eligible = (onwardByKey[`${pair.gateway}|${pair.dest}|${bridge.cabin}`] ?? []).filter(r => r.date >= pair.date);
      const onwardRow = eligible.length ? eligible.reduce((a, b) => (b.mileage < a.mileage ? b : a)) : null;
      if (onwardRow) {
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
}

// Every award leg expands at its own cabin: directs and gateway legs at the trip cabin,
// stitched onward legs at the cabin the cutoff picked. Dedup hybrid legs by id+cabin against the
// direct finalists and each other so a shared trip expands once; results join back via tripByKey.
const directItems = rows.map(r => ({ id: r.id, cabin, label: r.dest }));
const hybridItems = [];
const seenItem = new Set(directItems.map(it => `${it.id}|${it.cabin}`));
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
// Every business award leg flows through — direct finalists and hybrid legs alike — deduped by
// id+cabin; the verdict mutates the shared expanded trip, so both the finalist and any hybrid see it.
if (isBiz) {
  const verifyTargets = [];
  const seenVerify = new Set();
  const addVerify = (id, verifyCabin, date, label, trip) => {
    if (!trip || !(trip.product === 'verify' || (trip.product === 'unknown' && trip.longhaul))) return;
    const key = `${id}|${verifyCabin}`;
    if (seenVerify.has(key)) return;
    seenVerify.add(key);
    verifyTargets.push({ id, date, label, trip });
  };
  rows.forEach((row, i) => addVerify(row.id, cabin, row.date, row.dest, trips[i]));
  for (const h of composed) {
    addVerify(h.award.id, cabin, h.award.date, h.gateway, tripByKey[`${h.award.id}|${cabin}`]);
    if (h.onward.mode === 'award' && h.onward.cabin === 'business')
      addVerify(h.onward.id, h.onward.cabin, h.onward.date, h.dest, tripByKey[`${h.onward.id}|${h.onward.cabin}`]);
  }
  if (verifyTargets.length) {
    phase('Verify');
    const verified = await pipeline(verifyTargets, ({ id, date, label, trip }) => agent(
      `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to pin down the business hard product on ${trip.longhaul} departing ${date}: the carrier's seat map for that flight number and date, recent cabin reviews, retrofit trackers. Return:\n` +
      `- id: "${id}"\n` +
      `- product: the resolved verdict — one of suite, solid, dated, barely, unknown; return \`unknown\` when sources disagree\n` +
      `- productNote: the product name plus one clause describing the hard product.`,
      { label: `verify:${label}`, phase: 'Verify', schema: VERIFY_SCHEMA },
    ));
    // verifyTargets[i] aligns with verified[i]; mutate the shared trip so directs and hybrids both update.
    verifyTargets.forEach((t, i) => { if (verified[i]) { t.trip.product = verified[i].product; t.trip.productNote = verified[i].productNote; } });
  }
}

const candidates = rows.map((row, i) => ({ row, trip: trips[i] })).filter(c => c.trip && typeof c.trip.mileageCost === 'number');
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

// Transit phase: flag, never filter. Same-ticket connections (origins of segments[1:]) and hybrid
// gateways (a separate booking self-transfers landside, so an entry check); zero quota, documents-gated.
const segmentConnections = (t) => {
  if (!Array.isArray(t.segments) || t.segments.length === 0) throw new Error('plan-trip: expanded trip lacks segments');
  return [...new Set(t.segments.slice(1).map(s => s.split(' ')[1].split('-')[0]))];
};
const transitByPoint = {};
let transitRan = false;
if (hasDocuments) {
  const transitPoints = [];
  const seenPoint = new Set();
  const addPoint = (airport, kind) => {
    if (!IATA.test(airport)) throw new Error(`plan-trip: transit check-point airport failed the injection guard: ${airport}`);
    const key = `${airport}|${kind}`;
    if (seenPoint.has(key)) return;
    seenPoint.add(key);
    transitPoints.push({ airport, kind });
  };
  for (const { trip } of kept) for (const apt of segmentConnections(trip)) addPoint(apt, 'transit');
  for (const h of finalHybrids) {
    for (const apt of segmentConnections(h.award.trip)) addPoint(apt, 'transit');
    if (h.onward.mode === 'award') for (const apt of segmentConnections(h.onward.trip)) addPoint(apt, 'transit');
    addPoint(h.gateway, 'entry');
  }
  transitRan = transitPoints.length > 0;
  if (transitRan) {
    phase('Transit');
    const flags = await pipeline(transitPoints, pt => agent(
      (pt.kind === 'transit'
        ? `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to determine whether ${traveler} needs a transit (airside) visa to connect through airport ${pt.airport} on a single ticket. Prefer official government and airport sources. Note the hinges: sterile airside transit versus clearing immigration, terminal changes, and minimum layover length.\n\n`
        : `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to determine whether ${traveler} meets entry requirements for a landside self-transfer at airport ${pt.airport}: a separate booking forces leaving the sterile area and re-entering, so this is an entry, not airside transit. Prefer official government sources. Note the hinges: visa-on-arrival versus a pre-arranged visa, immigration and terminal layout, and layover length.\n\n`) +
      `Return:\n- airport: "${pt.airport}"\n` +
      `- risk: one of none, possible, required — none when nothing extra is needed, possible when it hinges on terminal, route, layover, or documents, required when a visa is clearly needed\n` +
      `- transitNote: one or two sentences on what the traveler must verify.`,
      { label: `transit:${pt.airport}`, phase: 'Transit', schema: TRANSIT_SCHEMA },
    ));
    // transitPoints[i] aligns with flags[i]; key by our validated code, never the agent-echoed one.
    transitPoints.forEach((pt, i) => {
      const f = flags[i];
      if (f && f.risk !== 'none') transitByPoint[`${pt.airport}|${pt.kind}`] = { airport: pt.airport, risk: f.risk, transitNote: f.transitNote };
    });
  }
}
skipped.transit = !transitRan;

const enrichByDest = {};
if (enrichRan) {
  phase('Enrich');
  const dests = [...new Set([...kept.map(c => c.row.dest), ...finalHybrids.map(h => h.dest)])];
  const enriched = await pipeline(dests, dest => agent(
    `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to research airport ${dest} for travel in ${startDate.slice(0, 7)}. Return:\n` +
    `- dest: "${dest}"\n- weather: the typical weather and season for that window\n` +
    `- visaNote: a short entry/visa note for ${traveler}\n- appeal: one or two sentences on how it fits a "${vibe}" trip.`,
    { label: `enrich:${dest}`, phase: 'Enrich', schema: ENRICH_SCHEMA },
  ));
  for (const e of enriched.filter(Boolean)) enrichByDest[e.dest] = e;
}

const finalists = kept.map(({ row, trip }) => {
  const enrich = enrichByDest[row.dest];
  const transit = segmentConnections(trip).map(apt => transitByPoint[`${apt}|transit`]).filter(Boolean);
  return { ...row, trip, ...(transit.length ? { transit } : {}), ...(enrich ? { enrich } : {}) };
});
const hybrids = finalHybrids.map(h => {
  const enrich = enrichByDest[h.dest];
  const conns = [...new Set([
    ...segmentConnections(h.award.trip),
    ...(h.onward.mode === 'award' ? segmentConnections(h.onward.trip) : []),
  ])];
  const transit = [transitByPoint[`${h.gateway}|entry`], ...conns.map(apt => transitByPoint[`${apt}|transit`])].filter(Boolean);
  // Cash onward legs (Bridge retains only `stops`, no connection airports); flag a stopped hop generically.
  if (h.onward.mode === 'cash' && h.onward.stops >= 1) transit.push({ airport: 'unknown', risk: 'possible', transitNote: 'One-stop cash leg — connection airport not identified by the quote; verify transit rules for the routing when booking.' });
  return { ...h, ...(transit.length ? { transit } : {}), ...(enrich ? { enrich } : {}) };
});

const allQuotas = [...quotas, ...expandResults.filter(Boolean).map(t => t.quota).filter(q => q >= 0)];
return { finalists, hybrids, sweepFiles, considered: shortlist.considered, quota: allQuotas.length ? Math.min(...allQuotas) : -1, skipped };
