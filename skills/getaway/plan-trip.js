export const meta = {
  name: 'getaway-plan-trip',
  description:
    'Award-trip planning fan-out: parallel seats.aero sweeps, an offline jq shortlist, per-finalist trip expansion, and optional vibe enrichment.',
  whenToUse:
    'Invoked by the getaway skill for planning asks spanning 2+ destination buckets or programs. Args come from stored preferences plus AskUserQuestion answers. Returns the merged finalists; the calling session builds the cc-present board.',
  phases: [
    { title: 'Sweep', detail: 'One agent per destination bucket or program runs a single seats.aero search/availability call into a scratchpad JSONL.' },
    { title: 'Shortlist', detail: 'One offline jq pass over every sweep file filters, dedups, and ranks down to the finalists — zero API calls.' },
    { title: 'Expand', detail: 'One agent per finalist expands its trip ID into bookable truth: integer miles, exact taxes, segments, booking link.' },
    { title: 'Enrich', detail: 'When a vibe is set, one WebSearch agent per destination adds weather, visa, and appeal color — no quota spent.' },
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
        required: ['id', 'date', 'origin', 'dest', 'source', 'mileage', 'seats', 'airlines', 'direct'],
        properties: {
          id: { type: 'string' }, date: { type: 'string' }, origin: { type: 'string' }, dest: { type: 'string' },
          source: { type: 'string' }, mileage: { type: 'number' }, seats: { type: 'number' },
          airlines: { type: 'string' }, direct: { type: 'boolean' },
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
  },
};
const ENRICH_SCHEMA = {
  type: 'object',
  required: ['dest', 'weather', 'visaNote', 'appeal'],
  properties: { dest: { type: 'string' }, weather: { type: 'string' }, visaNote: { type: 'string' }, appeal: { type: 'string' } },
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

if (a.vibe !== undefined && (typeof a.vibe !== 'string' || a.vibe.length === 0)) throw new Error('plan-trip: vibe must be a non-empty string');
const vibe = a.vibe;

const quotaFloor = a.quotaFloor ?? 100;
if (!Number.isInteger(quotaFloor)) throw new Error('plan-trip: quotaFloor must be an integer');

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
if (sweepResults.length - okSweeps.length > 0) log(`${sweepResults.length - okSweeps.length} sweep agent(s) died; their files are excluded`);

// The on-disk quota cache is last-writer-wins under a parallel burst, so trust the
// per-agent reports over `getaway.sh quota`.
const quotas = okSweeps.map(r => r.quota).filter(q => q >= 0);
const minQuota = quotas.length ? Math.min(...quotas) : -1;
const quotaLow = minQuota >= 0 && minQuota < quotaFloor;
const enrichRan = Boolean(vibe) && !quotaLow;
let expandCap = maxFinalists;
let expandTrimmedTo = null;
if (quotaLow) {
  expandCap = Math.min(maxFinalists, 3, minQuota);
  expandTrimmedTo = expandCap;
  log(`quota low (${minQuota} < floor ${quotaFloor}): trimming Expand to ${expandCap} finalists and skipping Enrich`);
}
const skipped = { enrich: !enrichRan, expandTrimmedTo };

if (sweepFiles.length === 0) {
  log('no sweep files produced; returning empty finalists');
  return { finalists: [], sweepFiles, considered: 0, quota: minQuota, skipped };
}

const avail = `.${jc}Available`;
const mileageF = `(.${jc}MileageCost|tonumber)`;
const seatsF = `(.${jc}RemainingSeats|tonumber)`;
const airlinesF = `.${jc}Airlines`;
const directF = `.${jc}Direct`;
const hard = JSON.stringify(avoidAirlines.filter(x => x.strength === 'hard').map(x => x.code));
const soft = JSON.stringify(avoidAirlines.filter(x => x.strength === 'soft').map(x => x.code));
const clauses = [
  `${avail} == true`,
  ...(mileageCeiling !== undefined ? [`${mileageF} <= ${mileageCeiling}`] : []),
  `${seatsF} >= ${travelers}`,
  `(.Route.OriginAirport as $o | (${JSON.stringify(origins)} | index($o)) != null)`,
  ...(sources.length ? [`(.Source as $s | (${JSON.stringify(sources)} | index($s)) != null)`] : []),
  `(.Route.DestinationAirport as $d | (${JSON.stringify(avoidDestinations)} | index($d)) | not)`,
  `(${airlinesF} | split(", ") | all(. as $c | ${hard} | index($c) == null))`,
].join('\n        and ');
const shortlistJq =
  `jq -s '{ considered: length,\n` +
  `  rows: ( [ .[] | select(${clauses})\n` +
  `            | { id: .ID, date: .Date, origin: .Route.OriginAirport, dest: .Route.DestinationAirport,\n` +
  `                source: .Source, mileage: ${mileageF}, seats: ${seatsF},\n` +
  `                airlines: ${airlinesF}, direct: ${directF},\n` +
  `                soft: (${airlinesF} | split(", ") | any(. as $c | ${soft} | index($c) != null)) } ]\n` +
  `          | group_by([.origin, .dest, .date, .source]) | map(sort_by(.soft, .mileage) | .[0])\n` +
  `          | sort_by(.soft, .mileage) | .[0:${maxFinalists}] | map(del(.soft)) ) }' ` +
  sweepFiles.map(f => `"${f}"`).join(' ');

phase('Shortlist');
const shortlist = await agent(
  `Run exactly this one Bash command — a single offline jq pass over the sweep files, zero API calls, run no getaway.sh command:\n\n${shortlistJq}\n\n` +
  `Return its JSON output: considered (total rows scanned) and rows (the ranked finalists, each with id, date, origin, dest, source, mileage, seats, airlines, direct). mileage and seats are numbers; direct is boolean.`,
  { label: 'shortlist', phase: 'Shortlist', schema: SHORTLIST_SCHEMA },
);
if (!shortlist || shortlist.rows.length === 0) {
  log('shortlist produced 0 rows; no finalists');
  return { finalists: [], sweepFiles, considered: shortlist?.considered ?? 0, quota: minQuota, skipped };
}

const rows = shortlist.rows.slice(0, expandCap);
for (const r of rows) if (!TRIP_ID.test(r.id)) throw new Error(`plan-trip: shortlist row id failed the injection guard: ${r.id}`);

// Re-check seats and connections against the bookable truth: the cached row's cheapest
// live trip can seat fewer travelers or connect through an avoided airport.
const transitClause = avoidTransit.length
  ? ` and ([.AvailabilitySegments[1:][].OriginAirport] | all(. as $x | (${JSON.stringify(avoidTransit)} | index($x)) == null))`
  : '';

phase('Expand');
const trips = await pipeline(rows, row => agent(
  `Run exactly this one Bash pipeline, nothing else:\n\n` +
  `"${script}" trip ${row.id} | jq '{ bookingPrimary: ([.booking_links[] | select(.primary) | .label][0]),\n` +
  `  best: (.data | map(select(.Cabin == "${cabin}" and .RemainingSeats >= ${travelers}${transitClause})) | min_by(.MileageCost)\n` +
  `    | { MileageCost, TotalTaxes, TaxesCurrency, RemainingSeats, FlightNumbers, UpdatedAt,\n` +
  `        segments: [.AvailabilitySegments[] | "\\(.FlightNumber) \\(.OriginAirport)-\\(.DestinationAirport) \\(.Cabin) (\\(.AircraftName))"] }) }'\n\n` +
  `It also prints \`quota remaining: N\` to stderr. If \`best\` is null — no trip in that cabin seats ` +
  `${travelers} traveler(s) and clears the connection rules — return only id and quota. Otherwise return:\n` +
  `- id: "${row.id}"\n- mileageCost: best.MileageCost\n- totalTaxes: best.TotalTaxes\n- taxesCurrency: best.TaxesCurrency\n` +
  `- remainingSeats: best.RemainingSeats\n- flightNumbers: best.FlightNumbers\n- segments: best.segments\n` +
  `- updatedAt: best.UpdatedAt\n- bookingPrimary: bookingPrimary\n- quota: the integer N from the stderr line, or -1 if absent.`,
  { label: `trip:${row.dest}`, phase: 'Expand', schema: TRIP_SCHEMA },
));

const enrichByDest = {};
if (enrichRan) {
  phase('Enrich');
  const dests = [...new Set(shortlist.rows.map(r => r.dest))];
  const enriched = await pipeline(dests, dest => agent(
    `Use WebSearch only — zero API/quota calls, run no getaway.sh command — to research airport ${dest} for travel in ${startDate.slice(0, 7)}. Return:\n` +
    `- dest: "${dest}"\n- weather: the typical weather and season for that window\n` +
    `- visaNote: a short entry/visa note for a US passport holder\n- appeal: one or two sentences on how it fits a "${vibe}" trip.`,
    { label: `enrich:${dest}`, phase: 'Enrich', schema: ENRICH_SCHEMA },
  ));
  for (const e of enriched.filter(Boolean)) enrichByDest[e.dest] = e;
}

const finalists = rows.map((row, i) => {
  const trip = trips[i];
  if (!trip || typeof trip.mileageCost !== 'number') return null;
  const enrich = enrichByDest[row.dest];
  return enrich ? { ...row, trip, enrich } : { ...row, trip };
}).filter(Boolean);
if (rows.length - finalists.length > 0) log(`${rows.length - finalists.length} finalist(s) dropped: expansion agent died or no live trip seats the party`);

const allQuotas = [...quotas, ...trips.filter(Boolean).map(t => t.quota).filter(q => q >= 0)];
return { finalists, sweepFiles, considered: shortlist.considered, quota: allQuotas.length ? Math.min(...allQuotas) : -1, skipped };
