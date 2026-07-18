import type { CSSProperties, ReactNode } from 'react';
import type { PackComponentProps, ThemeTokens } from './host/present';
import { tokens } from './host/present';
import type { Money } from './format';
import {
  dayOffsetSuffix,
  formatDuration,
  formatMilesByProgram,
  formatMilesExact,
  formatMoney,
  formatTaxes,
  relativeAge,
  wallClock,
} from './format';
import type { SeatVerdict } from './ui';
import { CapsLabel, Chip, LinkButton, SeatVerdictChip, cardShell } from './ui';
import { NoteBar } from './notes';

type Cabin = 'economy' | 'premium' | 'business' | 'first';

interface Seat {
  verdict: SeatVerdict;
  product?: string | null;
  note?: string | null;
  picks?: SeatAdvice[];
  avoids?: SeatAdvice[];
}

interface SeatAdvice {
  seat: string;
  why: string;
}

interface Flight {
  flightNumber: string;
  origin: string;
  destination: string;
  departsAt: string;
  arrivesAt: string;
  cabin?: Cabin;
  durationMinutes: number;
  aircraft?: string;
  aircraftCode?: string;
  seat?: Seat;
}

interface BookingLink {
  label: string;
  url: string;
  primary: boolean;
}

interface Leg {
  role: string;
  kind: 'award' | 'cash';
  program?: string;
  miles?: number;
  taxes?: Money[];
  taxesNote?: string;
  price?: Money;
  flights: Flight[];
  bookingLinks: BookingLink[];
  notes?: string[];
}

interface Transfer {
  from: string;
  to: string;
  amount: number;
  note?: string;
}

interface Totals {
  miles: { program: string; miles: number }[];
  cash: Money[];
}

interface BookingBlock {
  id: string;
  type: 'getaway.booking';
  title: string;
  subtitle?: string;
  fetchedAt: string;
  totals?: Totals;
  transfers?: Transfer[];
  legs: Leg[];
}

function mono(t: ThemeTokens): CSSProperties {
  return { fontFamily: t.fontMono, fontSize: '0.85rem' };
}

function numberBadge(t: ThemeTokens): CSSProperties {
  return {
    flexShrink: 0,
    width: '1.6rem',
    height: '1.6rem',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: '999px',
    fontFamily: t.fontMono,
    fontSize: '0.8rem',
    fontWeight: 600,
    color: t.accent,
    background: `color-mix(in srgb, ${t.accent} 12%, ${t.surface})`,
    border: `1px solid color-mix(in srgb, ${t.accent} 30%, ${t.border})`,
  };
}

function legHeadline(leg: Leg): string {
  if (leg.kind === 'award') {
    const taxesText = formatTaxes(leg.taxes ?? []);
    return `Book on ${leg.program} — ${formatMilesExact(leg.miles!)}${taxesText ? ` + ${taxesText}` : ''}`;
  }
  return `Buy cash — ${formatMoney(leg.price!)}`;
}

function StepRow({ n, children }: { n: number; children: ReactNode }) {
  const t = tokens();
  return (
    <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-start' }}>
      <div style={numberBadge(t)}>{n}</div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>{children}</div>
    </div>
  );
}

// Seat advice is the point of this board: picks read ok-toned, avoids danger-toned.
function PickAvoid({ seat }: { seat: Seat }) {
  const t = tokens();
  const picks = seat.picks ?? [];
  const avoids = seat.avoids ?? [];
  if (picks.length === 0 && avoids.length === 0) return null;
  return (
    <span style={{ fontSize: '0.8rem' }}>
      {picks.length > 0 && (
        <span style={{ color: t.ok }}>
          pick{' '}
          {picks.map((pick, index) => (
            <span key={pick.seat} title={pick.why}>
              {index > 0 && ' · '}
              {pick.seat}
            </span>
          ))}
        </span>
      )}
      {picks.length > 0 && avoids.length > 0 && <span style={{ color: t.dim }}> — </span>}
      {avoids.length > 0 && (
        <span style={{ color: t.danger }}>
          avoid{' '}
          {avoids.map((avoid, index) => (
            <span key={avoid.seat} title={avoid.why}>
              {index > 0 && ' · '}
              {avoid.seat}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

function FlightRow({ flight }: { flight: Flight }) {
  const t = tokens();
  const suffix = dayOffsetSuffix(flight.departsAt, flight.arrivesAt);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', flexWrap: 'wrap' }}>
        <span style={mono(t)}>{flight.flightNumber}</span>
        <span>
          {flight.origin} → {flight.destination}
        </span>
        <span style={mono(t)}>
          {wallClock(flight.departsAt)} → {wallClock(flight.arrivesAt)}
          {suffix && <sup style={{ color: t.dim, marginLeft: '0.15rem' }}>{suffix}</sup>}
        </span>
        {flight.cabin && <Chip>{flight.cabin}</Chip>}
        {(flight.aircraft || flight.aircraftCode) && (
          <span style={{ color: t.dim, fontSize: '0.8rem' }}>
            {flight.aircraft}
            {flight.aircraft && flight.aircraftCode ? ` (${flight.aircraftCode})` : flight.aircraftCode}
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: t.dim, fontSize: '0.8rem' }}>{formatDuration(flight.durationMinutes)}</span>
      </div>
      {flight.seat && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', paddingLeft: '0.6rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <SeatVerdictChip verdict={flight.seat.verdict} />
            {flight.seat.product && <span style={{ color: t.dim, fontSize: '0.8rem' }}>{flight.seat.product}</span>}
            <PickAvoid seat={flight.seat} />
          </div>
          {flight.seat.note && <span style={{ color: t.dim, fontSize: '0.78rem' }}>{flight.seat.note}</span>}
        </div>
      )}
    </div>
  );
}

function LegBody({ leg }: { leg: Leg }) {
  const t = tokens();
  const primaryLink = leg.bookingLinks.find((l) => l.primary);
  const secondaryLinks = leg.bookingLinks.filter((l) => l !== primaryLink);
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <Chip>{leg.role}</Chip>
        <span style={{ fontWeight: 600 }}>{legHeadline(leg)}</span>
      </div>
      {leg.taxesNote && <span style={{ color: t.dim, fontSize: '0.78rem' }}>{leg.taxesNote}</span>}
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {leg.flights.map((f) => (
          <FlightRow key={f.flightNumber + f.departsAt} flight={f} />
        ))}
      </div>
      {leg.notes?.map((note, i) => (
        <span key={i} style={{ color: t.dim, fontSize: '0.8rem' }}>
          • {note}
        </span>
      ))}
    </>
  );
}

export function Booking({ block, value, submit, disabled, context }: PackComponentProps) {
  const t = tokens();
  const bk = block as unknown as BookingBlock;
  const transfers = bk.transfers ?? [];
  const milesText = bk.totals ? formatMilesByProgram(bk.totals.miles) : '';
  const cashText = bk.totals ? formatTaxes(bk.totals.cash) : '';
  const showTotals = !!bk.totals && (milesText !== '' || cashText !== '');

  return (
    <div style={{ ...cardShell(t), opacity: context.closed ? 0.6 : 1, transition: 'opacity 120ms ease' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        <span style={{ fontWeight: 700, fontSize: '1.1rem' }}>{bk.title}</span>
        {bk.subtitle && <span style={{ color: t.dim, fontSize: '0.9rem' }}>{bk.subtitle}</span>}
        <span style={{ color: t.dim, fontSize: '0.78rem' }} title={bk.fetchedAt}>
          availability checked {relativeAge(bk.fetchedAt)}
        </span>
      </div>

      {showTotals && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '0.25rem',
            padding: '0.6rem 0.75rem',
            background: t.surfaceRaised,
            border: `1px solid ${t.border}`,
            borderRadius: t.radiusMd,
            fontFamily: t.fontMono,
            fontVariantNumeric: 'tabular-nums',
            fontSize: '0.85rem',
          }}
        >
          <CapsLabel>total</CapsLabel>
          {milesText && <span>{milesText}</span>}
          {cashText && <span style={{ color: t.dim }}>{cashText}</span>}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
        {transfers.map((tr, i) => (
          <StepRow key={`transfer-${i}`} n={i + 1}>
            <span style={{ fontWeight: 600 }}>
              Transfer {formatMilesExact(tr.amount)} {tr.from} → {tr.to}
            </span>
            {tr.note && <span style={{ color: t.dim, fontSize: '0.8rem' }}>{tr.note}</span>}
          </StepRow>
        ))}
        {bk.legs.map((leg, i) => (
          <StepRow key={`leg-${i}`} n={transfers.length + i + 1}>
            <LegBody leg={leg} />
          </StepRow>
        ))}
      </div>

      <NoteBar value={value} submit={submit} disabled={disabled} context={context} />
    </div>
  );
}
