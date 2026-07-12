import type { CSSProperties } from 'react';
import type { PackComponentProps } from './host/present';
import type { Money } from './format';
import { dayOffsetSuffix, formatDuration, formatMoney, wallClock } from './format';

interface FlightBlock {
  flightNumber: string;
  origin: string;
  destination: string;
  departsAt: string;
  arrivesAt: string;
  cabin: string;
  durationMinutes: number;
  aircraft?: string;
  price?: Money;
}

const timeStyle: CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: '1.6rem',
  fontWeight: 600,
  lineHeight: 1.1,
};

const airportStyle: CSSProperties = { color: 'var(--muted)', fontSize: '0.8rem' };

const chipStyle: CSSProperties = {
  fontSize: '0.7rem',
  textTransform: 'capitalize',
  padding: '0.1rem 0.45rem',
  borderRadius: 'var(--radius-md)',
  color: 'var(--muted)',
  border: '1px solid var(--border)',
};

export function Flight({ block }: PackComponentProps) {
  const flight = block as unknown as FlightBlock;
  const suffix = dayOffsetSuffix(flight.departsAt, flight.arrivesAt);

  return (
    <div
      style={{
        position: 'relative',
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
      {flight.price && (
        <span
          style={{
            position: 'absolute',
            top: '0.6rem',
            right: '0.75rem',
            fontWeight: 600,
            fontSize: '0.85rem',
            padding: '0.1rem 0.5rem',
            borderRadius: '999px',
            color: 'var(--accent)',
            background: 'color-mix(in srgb, var(--accent) 12%, var(--surface))',
          }}
        >
          {formatMoney(flight.price)}
        </span>
      )}

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: '1rem' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={timeStyle}>{wallClock(flight.departsAt)}</div>
          <div style={airportStyle}>{flight.origin}</div>
        </div>
        <div style={{ flex: 1, textAlign: 'center', paddingBottom: '0.5rem' }}>
          <div style={{ color: 'var(--muted)', fontSize: '0.75rem', marginBottom: '0.2rem' }}>
            {formatDuration(flight.durationMinutes)}
          </div>
          <div style={{ borderTop: '1px solid var(--border)' }} />
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={timeStyle}>
            {wallClock(flight.arrivesAt)}
            {suffix && <sup style={{ color: 'var(--muted)', fontSize: '0.9rem', marginLeft: '0.15rem' }}>{suffix}</sup>}
          </div>
          <div style={airportStyle}>{flight.destination}</div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem' }}>{flight.flightNumber}</span>
        <span style={chipStyle}>{flight.cabin}</span>
        {flight.aircraft && <span style={airportStyle}>{flight.aircraft}</span>}
      </div>
    </div>
  );
}
