import type { CSSProperties } from 'react';
import type { Money } from './format';
import type { PackComponentProps } from './host/present';
import type { SeatVerdict } from './ui';
import { tokens } from './host/present';
import { dayOffsetSuffix, formatDuration, formatMoney, wallClock } from './format';
import { cardShell, Chip, SeatVerdictChip } from './ui';
import { NoteBar } from './notes';

interface SeatQuality {
  verdict: SeatVerdict;
  product?: string | null;
  note?: string | null;
}

interface FlightBlock {
  flightNumber: string;
  origin: string;
  destination: string;
  departsAt: string;
  arrivesAt: string;
  cabin: string;
  durationMinutes: number;
  aircraft?: string;
  aircraftCode?: string;
  seatQuality?: SeatQuality;
  price?: Money;
}

export function Flight({ block, value, submit, disabled, context }: PackComponentProps) {
  const t = tokens();
  const flight = block as unknown as FlightBlock;
  const suffix = dayOffsetSuffix(flight.departsAt, flight.arrivesAt);
  const timeStyle: CSSProperties = { fontFamily: t.fontMono, fontSize: '1.6rem', fontWeight: 600, lineHeight: 1.1 };
  const airportStyle: CSSProperties = { color: t.dim, fontSize: '0.8rem' };

  return (
    <div style={{ ...cardShell(t), position: 'relative' }}>
      {flight.price && (
        <span
          style={{
            position: 'absolute',
            top: '0.7rem',
            right: '0.85rem',
            fontWeight: 600,
            fontSize: '0.85rem',
            padding: '0.1rem 0.5rem',
            borderRadius: '999px',
            color: t.accent,
            background: `color-mix(in srgb, ${t.accent} 12%, ${t.surface})`,
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
          <div style={{ color: t.dim, fontSize: '0.75rem', marginBottom: '0.2rem' }}>
            {formatDuration(flight.durationMinutes)}
          </div>
          <div style={{ borderTop: `1px solid ${t.border}` }} />
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={timeStyle}>
            {wallClock(flight.arrivesAt)}
            {suffix && <sup style={{ color: t.dim, fontSize: '0.9rem', marginLeft: '0.15rem' }}>{suffix}</sup>}
          </div>
          <div style={airportStyle}>{flight.destination}</div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ fontFamily: t.fontMono, fontSize: '0.85rem' }}>{flight.flightNumber}</span>
        <Chip>{flight.cabin}</Chip>
        {flight.aircraft && <span style={airportStyle}>{flight.aircraft}</span>}
        {flight.aircraftCode && <span style={{ ...airportStyle, fontFamily: t.fontMono }}>{flight.aircraftCode}</span>}
        {flight.seatQuality && (
          <span
            title={flight.seatQuality.note ?? undefined}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}
          >
            <SeatVerdictChip verdict={flight.seatQuality.verdict} />
            {flight.seatQuality.product && (
              <span style={{ color: t.dim, fontSize: '0.8rem' }}>{flight.seatQuality.product}</span>
            )}
          </span>
        )}
      </div>

      <NoteBar value={value} submit={submit} disabled={disabled} context={context} />
    </div>
  );
}
