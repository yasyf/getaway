import type { CSSProperties } from 'react';
import type { PackComponentProps } from './host/present';
import type { Money } from './format';
import {
  dayOffsetSuffix,
  formatDuration,
  formatMilesExact,
  formatMoney,
  relativeAge,
  wallClock,
} from './format';

interface Segment {
  flightNumber: string;
  origin: string;
  destination: string;
  departsAt: string;
  arrivesAt: string;
  cabin: string;
  aircraft: string;
  durationMinutes: number;
}

interface ItineraryBlock {
  program: string;
  miles: number;
  taxes: Money;
  remainingSeats: number;
  bookingLink: { label: string; url: string };
  updatedAt: string;
  totalDurationMinutes: number;
  segments: Segment[];
}

const badgeStyle: CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: '0.7rem',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  padding: '0.1rem 0.5rem',
  borderRadius: '999px',
  color: 'var(--accent)',
  background: 'color-mix(in srgb, var(--accent) 12%, var(--surface))',
  border: '1px solid color-mix(in srgb, var(--accent) 30%, var(--border))',
};

const chipStyle: CSSProperties = {
  fontSize: '0.7rem',
  textTransform: 'capitalize',
  padding: '0.1rem 0.45rem',
  borderRadius: 'var(--radius-md)',
  color: 'var(--muted)',
  border: '1px solid var(--border)',
};

const monoStyle: CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: '0.85rem' };

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

function SegmentRow({ seg }: { seg: Segment }) {
  const suffix = dayOffsetSuffix(seg.departsAt, seg.arrivesAt);
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', flexWrap: 'wrap' }}>
      <span style={monoStyle}>{seg.flightNumber}</span>
      <span>
        {seg.origin} → {seg.destination}
      </span>
      <span style={monoStyle}>
        {wallClock(seg.departsAt)} → {wallClock(seg.arrivesAt)}
        {suffix && <sup style={{ color: 'var(--muted)', marginLeft: '0.15rem' }}>{suffix}</sup>}
      </span>
      <span style={chipStyle}>{seg.cabin}</span>
      <span style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>{seg.aircraft}</span>
      <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: '0.8rem' }}>
        {formatDuration(seg.durationMinutes)}
      </span>
    </div>
  );
}

export function Itinerary({ block }: PackComponentProps) {
  const itin = block as unknown as ItineraryBlock;
  const first = itin.segments[0]!;
  const last = itin.segments[itin.segments.length - 1]!;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.75rem',
        color: 'var(--text)',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        padding: '1rem',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>
            {first.origin} → {last.destination}
          </span>
          <span style={badgeStyle}>{itin.program}</span>
        </div>
        <div style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
          <span style={{ fontWeight: 600 }}>{formatMilesExact(itin.miles)} miles</span>
          <span style={{ color: 'var(--muted)' }}> + {formatMoney(itin.taxes)}</span>
        </div>
      </div>

      <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>
        {itin.remainingSeats} seats · {formatDuration(itin.totalDurationMinutes)} ·{' '}
        <span title={itin.updatedAt}>checked {relativeAge(itin.updatedAt)}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {itin.segments.map((seg, i) => {
          const next = itin.segments[i + 1];
          const chains = next && seg.destination === next.origin;
          return (
            <div key={seg.flightNumber + seg.departsAt} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <SegmentRow seg={seg} />
              {next &&
                (chains ? (
                  <div
                    style={{
                      color: 'var(--muted)',
                      fontSize: '0.75rem',
                      paddingLeft: '0.6rem',
                      borderLeft: '2px dashed var(--border)',
                    }}
                  >
                    {formatDuration(wallClockMinutes(next.departsAt) - wallClockMinutes(seg.arrivesAt))} layover in{' '}
                    {seg.destination}
                  </div>
                ) : (
                  <div style={{ borderTop: '1px solid var(--border)' }} />
                ))}
            </div>
          );
        })}
      </div>

      <a
        href={itin.bookingLink.url}
        target="_blank"
        rel="noopener noreferrer"
        style={{ color: 'var(--accent)', fontWeight: 600, textDecoration: 'none' }}
      >
        {itin.bookingLink.label}
      </a>
    </div>
  );
}
