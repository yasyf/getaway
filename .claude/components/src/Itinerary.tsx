import type { CSSProperties } from 'react';
import type { PackComponentProps, ThemeTokens } from './host/present';
import { tokens } from './host/present';
import type { SeatVerdict } from './ui';
import { Badge, Chip, Disclosure, LinkButton, SeatVerdictChip, cardShell } from './ui';
import { NoteBar } from './notes';
import { dayOffsetSuffix, formatDuration, formatMilesExact, formatTaxes, relativeAge, wallClock } from './format';

type Cabin = 'economy' | 'premium' | 'business' | 'first';

interface TaxLine {
  amount: number;
  currency: string;
}

interface SeatQuality {
  verdict: SeatVerdict;
  product?: string | null;
  note?: string | null;
}

interface BookingLink {
  label: string;
  url: string;
  primary: boolean;
}

interface Segment {
  flightNumber: string;
  origin: string;
  destination: string;
  departsAt: string;
  arrivesAt: string;
  cabin: Cabin;
  aircraft: string;
  aircraftCode?: string;
  seatQuality?: SeatQuality;
  durationMinutes: number;
}

interface ItineraryBlock {
  id: string;
  type: 'getaway.itinerary';
  program: string;
  miles: number;
  taxes: TaxLine[];
  taxesNote?: string;
  remainingSeats: number;
  bookingLinks: BookingLink[];
  fetchedAt: string;
  totalDurationMinutes: number;
  segments: Segment[];
}

// Only warn (dated) and danger (barely) verdicts earn a summary chip; ok and
// neutral stay quiet. barely outranks dated when a trip mixes both.
const CONCERN_RANK: Record<SeatVerdict, number> = { barely: 2, dated: 1, suite: 0, solid: 0, verify: 0 };

function mono(t: ThemeTokens): CSSProperties {
  return { fontFamily: t.fontMono, fontSize: '0.85rem' };
}

// Naive wall-clock minutes: treat the stamp as if UTC. For two stamps at the same
// airport the offset cancels on subtraction, so a layover comes out right.
function wallClockMinutes(iso: string): number {
  return (
    Date.UTC(
      Number(iso.slice(0, 4)),
      Number(iso.slice(5, 7)) - 1,
      Number(iso.slice(8, 10)),
      Number(iso.slice(11, 13)),
      Number(iso.slice(14, 16)),
    ) / 60000
  );
}

function worstConcern(segments: Segment[]): SeatVerdict | null {
  let worst: SeatVerdict | null = null;
  for (const { seatQuality } of segments) {
    const v = seatQuality?.verdict;
    if (v && CONCERN_RANK[v] > 0 && (worst === null || CONCERN_RANK[v] > CONCERN_RANK[worst])) worst = v;
  }
  return worst;
}

function segmentsChain(segment: Segment, next: Segment): boolean {
  return segment.destination === next.origin;
}

function stopsLabel(segments: Segment[]): string {
  const stops = segments.slice(0, -1).filter((segment, i) => segmentsChain(segment, segments[i + 1]!));
  if (stops.length === 0) return 'nonstop';
  if (stops.length === 1) return `1 stop (${stops[0]!.destination})`;
  return `${stops.length} stops`;
}

function SegmentRow({ seg }: { seg: Segment }) {
  const t = tokens();
  const suffix = dayOffsetSuffix(seg.departsAt, seg.arrivesAt);
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', flexWrap: 'wrap' }}>
      <span style={mono(t)}>{seg.flightNumber}</span>
      <span>
        {seg.origin} → {seg.destination}
      </span>
      <span style={mono(t)}>
        {wallClock(seg.departsAt)} → {wallClock(seg.arrivesAt)}
        {suffix && <sup style={{ color: t.dim, marginLeft: '0.15rem' }}>{suffix}</sup>}
      </span>
      <Chip>{seg.cabin}</Chip>
      <span style={{ color: t.dim, fontSize: '0.8rem' }}>
        {seg.aircraft}
        {seg.aircraftCode ? ` (${seg.aircraftCode})` : ''}
      </span>
      {seg.seatQuality && (
        <span
          title={seg.seatQuality.note ?? undefined}
          style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
        >
          <SeatVerdictChip verdict={seg.seatQuality.verdict} />
          {seg.seatQuality.product && <span style={{ color: t.dim, fontSize: '0.8rem' }}>{seg.seatQuality.product}</span>}
        </span>
      )}
      <span style={{ marginLeft: 'auto', color: t.dim, fontSize: '0.8rem' }}>{formatDuration(seg.durationMinutes)}</span>
    </div>
  );
}

// Chained segments (arrival airport === next departure airport) get a dashed
// layover divider with the wall-clock gap and city; an open jaw gets a plain rule.
function SegmentList({ segments }: { segments: Segment[] }) {
  const t = tokens();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {segments.map((seg, i) => {
        const next = segments[i + 1];
        const chains = next && segmentsChain(seg, next);
        return (
          <div key={seg.flightNumber + seg.departsAt} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <SegmentRow seg={seg} />
            {next &&
              (chains ? (
                <div
                  style={{
                    color: t.dim,
                    fontSize: '0.75rem',
                    paddingLeft: '0.6rem',
                    borderLeft: `2px dashed ${t.border}`,
                  }}
                >
                  {formatDuration(wallClockMinutes(next.departsAt) - wallClockMinutes(seg.arrivesAt))} layover in{' '}
                  {seg.destination}
                </div>
              ) : (
                <div style={{ borderTop: `1px solid ${t.border}` }} />
              ))}
          </div>
        );
      })}
    </div>
  );
}

export function Itinerary({ block, value, submit, disabled, context }: PackComponentProps) {
  const t = tokens();
  const itin = block as unknown as ItineraryBlock;
  const first = itin.segments[0]!;
  const last = itin.segments[itin.segments.length - 1]!;
  const openJaw = itin.segments.slice(0, -1).some((segment, i) => !segmentsChain(segment, itin.segments[i + 1]!));
  const worst = worstConcern(itin.segments);
  const taxesText = formatTaxes(itin.taxes);
  const primaryLink = itin.bookingLinks.find((l) => l.primary);
  const secondaryLinks = itin.bookingLinks.filter((l) => l !== primaryLink);

  return (
    <div style={{ ...cardShell(t), opacity: context.closed ? 0.6 : 1, transition: 'opacity 120ms ease' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>
            {first.origin} → {last.destination}
          </span>
          <Badge>{itin.program}</Badge>
        </div>
        <div
          style={{
            marginLeft: 'auto',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-end',
            fontFamily: t.fontMono,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{formatMilesExact(itin.miles)} miles</span>
          {taxesText ? (
            <span style={{ color: t.dim, fontSize: '0.8rem' }}>+ {taxesText}</span>
          ) : itin.taxesNote ? (
            <span style={{ color: t.dim, fontSize: '0.8rem', fontFamily: t.fontProse }}>{itin.taxesNote}</span>
          ) : null}
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.4rem', color: t.dim, fontSize: '0.8rem' }}>
        <span>{formatDuration(itin.totalDurationMinutes)}</span>
        <span aria-hidden>·</span>
        <span>{stopsLabel(itin.segments)}</span>
        {openJaw && (
          <>
            <span aria-hidden>·</span>
            <span>open jaw</span>
          </>
        )}
        <span aria-hidden>·</span>
        <span>
          {itin.remainingSeats} {itin.remainingSeats === 1 ? 'seat' : 'seats'}
        </span>
        {worst && (
          <>
            <span aria-hidden>·</span>
            <SeatVerdictChip verdict={worst} />
          </>
        )}
        <span aria-hidden>·</span>
        <span title={itin.fetchedAt}>checked {relativeAge(itin.fetchedAt)}</span>
      </div>

      <Disclosure label="flights & booking">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <SegmentList segments={itin.segments} />
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.75rem' }}>
            {primaryLink && (
              <LinkButton href={primaryLink.url} primary>
                {primaryLink.label}
              </LinkButton>
            )}
            {secondaryLinks.map((l) => (
              <LinkButton key={l.url} href={l.url}>
                {l.label}
              </LinkButton>
            ))}
          </div>
          {itin.taxesNote && <div style={{ color: t.dim, fontSize: '0.78rem' }}>{itin.taxesNote}</div>}
        </div>
      </Disclosure>

      <NoteBar value={value} submit={submit} disabled={disabled} context={context} />
    </div>
  );
}
