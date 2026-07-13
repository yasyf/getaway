import type { CSSProperties } from 'react';
import type { PackComponentProps } from './host/present';
import { formatDateShort, formatMilesExact, formatMoney, relativeAge } from './format';

type AwardClass = 'standard' | 'suite';
type Session = 'pro' | 'anonymous';
type SearchState =
  | 'complete'
  | 'searched_empty'
  | 'night_clamped'
  | 'bot_wall'
  | 'logged_out'
  | 'date_in_past'
  | 'geocode_miss'
  | 'failed';
type DeferReason = 'no_checkout' | 'date_in_past' | 'invalid_interval' | 'not_walked';

interface Offer {
  awardClass: AwardClass;
  pointsPerNight: number | null;
  cashPerNightCents: number | null;
  centsPerPoint: number | null;
}

interface Room {
  program: string;
  name: string;
  currency: string;
  checkedAt: string;
  stale: boolean;
  offers: Offer[];
}

interface Interval {
  checkIn: string;
  checkOut: string;
  nights: number;
  nightClamped: boolean;
  requestedNights?: number;
}

interface SearchedBlock {
  state: 'searched';
  destination: string;
  airport?: string;
  session: Session;
  checkedAt: string;
  searchState: SearchState;
  interval: Interval;
  rooms: Room[];
}

interface DeferredBlock {
  state: 'deferred';
  reason: DeferReason;
  destination?: string;
  airport?: string;
}

type StayBlock = SearchedBlock | DeferredBlock;

// A search that reached rooms.aero but couldn't complete: honest failure, never
// "no rooms". searched_empty (looked, found nothing) is deliberately excluded.
const FAIL_REASON: Partial<Record<SearchState, string>> = {
  bot_wall: 'rooms.aero blocked the lookup with a bot wall',
  logged_out: 'the rooms.aero session was not signed in',
  geocode_miss: 'the destination could not be geocoded',
  date_in_past: 'check-in had already passed',
  failed: 'the lodging lookup did not complete',
};

const DEFER: Record<DeferReason, { head: string; body: string }> = {
  no_checkout: { head: 'Lodging deferred', body: 'No confirmed return date yet, so there is no checkout to price a stay against.' },
  date_in_past: { head: 'Lodging deferred', body: 'Check-in falls in the past.' },
  invalid_interval: { head: 'Lodging deferred', body: "The derived stay interval isn't valid." },
  not_walked: { head: 'Lodging not checked', body: 'This option was not walked on rooms.aero.' },
};

const cardStyle: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.75rem',
  color: 'var(--text)',
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  padding: '1rem',
};

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

const warnChip: CSSProperties = {
  fontSize: '0.68rem',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  padding: '0.1rem 0.45rem',
  borderRadius: '999px',
  color: 'var(--warn)',
  background: 'color-mix(in srgb, var(--warn) 14%, var(--surface))',
  border: '1px solid color-mix(in srgb, var(--warn) 45%, var(--border))',
};

const noticeStyle = (tone: 'warn' | 'danger'): CSSProperties => ({
  fontSize: '0.8rem',
  color: `var(--${tone})`,
  background: `color-mix(in srgb, var(--${tone}) 12%, var(--surface))`,
  border: `1px solid color-mix(in srgb, var(--${tone}) 45%, var(--border))`,
  borderRadius: 'var(--radius-md)',
  padding: '0.5rem 0.65rem',
});

const monoStrong: CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 600 };

function Header({ destination, airport, session }: { destination: string; airport?: string; session?: Session }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
      <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>{destination}</span>
      {airport && <span style={chipStyle}>{airport}</span>}
      {session === 'anonymous' && <span style={warnChip}>anonymous</span>}
    </div>
  );
}

// Per-night is source of truth. Points and cash each render only when present; the
// muted estimate multiplies per-night by nights and always reads "est.", never a quote.
function OfferRow({ offer, currency, nights }: { offer: Offer; currency: string; nights: number }) {
  const perNight: string[] = [];
  const estimate: string[] = [];
  if (offer.pointsPerNight !== null) {
    perNight.push(`${formatMilesExact(offer.pointsPerNight)} pts`);
    estimate.push(`${formatMilesExact(offer.pointsPerNight * nights)} pts`);
  }
  if (offer.cashPerNightCents !== null) {
    perNight.push(formatMoney({ amount: offer.cashPerNightCents, currency }));
    estimate.push(formatMoney({ amount: offer.cashPerNightCents * nights, currency }));
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={chipStyle}>{offer.awardClass}</span>
        <span style={monoStrong}>{perNight.join(' + ')}</span>
        <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>/ night</span>
        {offer.centsPerPoint !== null && (
          <span style={{ ...chipStyle, fontFamily: 'var(--font-mono)' }}>{offer.centsPerPoint.toFixed(1)}¢/pt</span>
        )}
      </div>
      <div style={{ color: 'var(--muted)', fontSize: '0.72rem' }}>
        ≈ {estimate.join(' + ')} est. for {nights} {nights === 1 ? 'night' : 'nights'}
      </div>
    </div>
  );
}

function Searched({ block }: { block: SearchedBlock }) {
  const { interval } = block;
  const clampNote = interval.nightClamped
    ? interval.requestedNights
      ? `first ${interval.nights} nights of ${interval.requestedNights}`
      : `capped at rooms.aero's ${interval.nights}-night maximum`
    : null;
  const failure = block.rooms.length === 0 ? FAIL_REASON[block.searchState] : undefined;

  return (
    <div style={cardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
        <Header destination={block.destination} airport={block.airport} session={block.session} />
        <span style={{ color: 'var(--muted)', fontSize: '0.8rem' }} title={block.checkedAt}>
          checked {relativeAge(block.checkedAt)}
        </span>
      </div>

      <div style={{ color: 'var(--muted)', fontSize: '0.8rem' }}>
        {formatDateShort(interval.checkIn)} → {formatDateShort(interval.checkOut)} · {interval.nights}{' '}
        {interval.nights === 1 ? 'night' : 'nights'}
        {clampNote && <span style={{ color: 'var(--warn)' }}> · {clampNote}</span>}
      </div>

      {block.session === 'anonymous' && (
        <div style={noticeStyle('warn')}>Anonymous session — these rates are not refreshed and can be weeks stale.</div>
      )}

      {failure ? (
        <div style={noticeStyle('danger')}>Lodging lookup couldn't complete: {failure}.</div>
      ) : block.rooms.length === 0 ? (
        <div style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>No award rooms found for this stay.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {block.rooms.map((room, i) => (
            <div key={room.program + room.name} style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {i > 0 && <div style={{ borderTop: '1px solid var(--border)' }} />}
              <RoomBody room={room} nights={interval.nights} />
            </div>
          ))}
        </div>
      )}

      <div style={{ color: 'var(--muted)', fontSize: '0.72rem' }}>
        Per-night rates are the source of truth; any stay total is an estimate.
      </div>
    </div>
  );
}

function RoomBody({ room, nights }: { room: Room; nights: number }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={badgeStyle}>{room.program}</span>
        <span style={{ fontWeight: 600 }}>{room.name}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {room.stale && <span style={warnChip}>stale</span>}
          <span title={room.checkedAt} style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
            checked {relativeAge(room.checkedAt)}
          </span>
        </span>
      </div>
      {room.offers.map((offer) => (
        <OfferRow key={offer.awardClass} offer={offer} currency={room.currency} nights={nights} />
      ))}
    </div>
  );
}

function Deferred({ block }: { block: DeferredBlock }) {
  const { head, body } = DEFER[block.reason];
  return (
    <div style={cardStyle}>
      {block.destination && <Header destination={block.destination} airport={block.airport} />}
      <div style={noticeStyle('warn')}>
        <span style={{ fontWeight: 600 }}>{head}.</span> {body}
      </div>
    </div>
  );
}

export function Stay({ block }: PackComponentProps) {
  const stay = block as unknown as StayBlock;
  return stay.state === 'deferred' ? <Deferred block={stay} /> : <Searched block={stay} />;
}
