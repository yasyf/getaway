import type { CSSProperties, ReactNode } from 'react';
import type { PackComponentProps, ThemeTokens } from './host/present';
import { tokens } from './host/present';
import { formatDateShort, formatMilesExact, formatMoney, relativeAge } from './format';
import { Badge, cardShell, Chip, Disclosure, ToneChip } from './ui';
import { NoteBar } from './notes';

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
type DeferReason = 'no_checkout' | 'open_jaw_stop' | 'date_in_past' | 'invalid_interval' | 'not_walked';

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

interface BestValue {
  points: number;
  centsPerPoint: number | null;
  name: string;
}

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
  open_jaw_stop: { head: 'Lodging deferred', body: 'The next leg departs from a different airport, so there is no checkout to price a stay against.' },
  date_in_past: { head: 'Lodging deferred', body: 'Check-in falls in the past.' },
  invalid_interval: { head: 'Lodging deferred', body: "The derived stay interval isn't valid." },
  not_walked: { head: 'Lodging not checked', body: 'This option was not walked on rooms.aero.' },
};

function noticeStyle(t: ThemeTokens, tone: 'warn' | 'danger'): CSSProperties {
  const c = tone === 'warn' ? t.warn : t.danger;
  return {
    fontSize: '0.8rem',
    color: c,
    background: `color-mix(in srgb, ${c} 12%, ${t.surface})`,
    border: `1px solid color-mix(in srgb, ${c} 45%, ${t.border})`,
    borderRadius: t.radiusMd,
    padding: '0.5rem 0.65rem',
  };
}

// The cheapest points-per-night offer across every room; null when no room
// quotes a points rate (an all-cash walk).
function bestValue(rooms: Room[]): BestValue | null {
  let best: BestValue | null = null;
  for (const room of rooms) {
    for (const offer of room.offers) {
      if (offer.pointsPerNight === null) continue;
      if (!best || offer.pointsPerNight < best.points) {
        best = { points: offer.pointsPerNight, centsPerPoint: offer.centsPerPoint, name: room.name };
      }
    }
  }
  return best;
}

function Header({ destination, airport, session }: { destination: string; airport?: string; session?: Session }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
      <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>{destination}</span>
      {airport && <Chip>{airport}</Chip>}
      {session === 'anonymous' && <ToneChip tone="warn">anonymous</ToneChip>}
    </div>
  );
}

// Per-night is source of truth. Points and cash each render only when present; the
// muted estimate multiplies per-night by nights and always reads "est.", never a quote.
function OfferRow({ offer, currency, nights }: { offer: Offer; currency: string; nights: number }) {
  const t = tokens();
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
        <Chip>{offer.awardClass}</Chip>
        <span style={{ fontFamily: t.fontMono, fontSize: '0.85rem', fontWeight: 600 }}>{perNight.join(' + ')}</span>
        <span style={{ color: t.dim, fontSize: '0.75rem' }}>/ night</span>
        {offer.centsPerPoint !== null && (
          <span
            style={{
              fontSize: '0.7rem',
              fontFamily: t.fontMono,
              padding: '0.1rem 0.45rem',
              borderRadius: t.radiusMd,
              color: t.dim,
              border: `1px solid ${t.border}`,
            }}
          >
            {offer.centsPerPoint.toFixed(1)}¢/pt
          </span>
        )}
      </div>
      <div style={{ color: t.dim, fontSize: '0.72rem' }}>
        ≈ {estimate.join(' + ')} est. for {nights} {nights === 1 ? 'night' : 'nights'}
      </div>
    </div>
  );
}

function RoomBody({ room, nights }: { room: Room; nights: number }) {
  const t = tokens();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <Badge>{room.program}</Badge>
        <span style={{ fontWeight: 600 }}>{room.name}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {room.stale && <ToneChip tone="warn">stale</ToneChip>}
          <span title={room.checkedAt} style={{ color: t.dim, fontSize: '0.75rem' }}>
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

function Searched({ block, note, closed }: { block: SearchedBlock; note: ReactNode; closed: boolean }) {
  const t = tokens();
  const { interval } = block;
  const clampNote = interval.nightClamped
    ? interval.requestedNights
      ? `first ${interval.nights} nights of ${interval.requestedNights}`
      : `capped at rooms.aero's ${interval.nights}-night maximum`
    : null;
  const failure = block.rooms.length === 0 ? FAIL_REASON[block.searchState] : undefined;
  const best = bestValue(block.rooms);

  return (
    <div style={{ ...cardShell(t), opacity: closed ? 0.6 : 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
        <Header destination={block.destination} airport={block.airport} session={block.session} />
        <span style={{ color: t.dim, fontSize: '0.8rem' }} title={block.checkedAt}>
          checked {relativeAge(block.checkedAt)}
        </span>
      </div>

      <div style={{ color: t.dim, fontSize: '0.8rem' }}>
        {formatDateShort(interval.checkIn)} → {formatDateShort(interval.checkOut)} · {interval.nights}{' '}
        {interval.nights === 1 ? 'night' : 'nights'}
        {clampNote && <span style={{ color: t.warn }}> · {clampNote}</span>}
      </div>

      {block.rooms.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', flexWrap: 'wrap', fontSize: '0.85rem' }}>
          {best && (
            <>
              <span style={{ fontWeight: 600 }}>from {formatMilesExact(best.points)} pts/night</span>
              {best.centsPerPoint !== null && <span style={{ color: t.dim }}>· {best.centsPerPoint.toFixed(1)}¢/pt</span>}
              <span style={{ color: t.dim }}>· {best.name}</span>
            </>
          )}
          <span style={{ marginLeft: 'auto', color: t.dim, fontSize: '0.78rem' }}>
            {block.rooms.length} {block.rooms.length === 1 ? 'property' : 'properties'}
          </span>
        </div>
      )}

      {block.session === 'anonymous' && (
        <div style={noticeStyle(t, 'warn')}>Anonymous session — these rates are not refreshed and can be weeks stale.</div>
      )}

      {failure ? (
        <div style={noticeStyle(t, 'danger')}>Lodging lookup couldn't complete: {failure}.</div>
      ) : block.rooms.length === 0 ? (
        <div style={{ color: t.dim, fontSize: '0.85rem' }}>No award rooms found for this stay.</div>
      ) : (
        <>
          <Disclosure label="show room detail">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.5rem' }}>
              {block.rooms.map((room, i) => (
                <div key={room.program + room.name} style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  {i > 0 && <div style={{ borderTop: `1px solid ${t.border}` }} />}
                  <RoomBody room={room} nights={interval.nights} />
                </div>
              ))}
            </div>
          </Disclosure>
          <div style={{ color: t.dim, fontSize: '0.72rem' }}>
            Per-night rates are the source of truth; any stay total is an estimate.
          </div>
        </>
      )}

      {note}
    </div>
  );
}

function Deferred({ block, note, closed }: { block: DeferredBlock; note: ReactNode; closed: boolean }) {
  const t = tokens();
  const { head, body } = DEFER[block.reason];
  return (
    <div style={{ ...cardShell(t), opacity: closed ? 0.6 : 1 }}>
      {block.destination && <Header destination={block.destination} airport={block.airport} />}
      <div style={noticeStyle(t, 'warn')}>
        <span style={{ fontWeight: 600 }}>{head}.</span> {body}
      </div>
      {note}
    </div>
  );
}

export function Stay({ block, value, submit, disabled, context }: PackComponentProps) {
  const stay = block as unknown as StayBlock;
  const note = <NoteBar value={value} submit={submit} disabled={disabled} context={context} />;
  return stay.state === 'deferred' ? (
    <Deferred block={stay} note={note} closed={context.closed} />
  ) : (
    <Searched block={stay} note={note} closed={context.closed} />
  );
}
