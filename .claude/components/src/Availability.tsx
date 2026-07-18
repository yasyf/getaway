import { Fragment, useState } from 'react';
import type { PackComponentProps } from './host/present';
import { tokens } from './host/present';
import { formatDateShort, formatMilesCompact } from './format';
import { Badge, CapsLabel, capsStyle, cardShell, Disclosure } from './ui';
import { NoteBar } from './notes';

type Cabin = 'economy' | 'premium' | 'business' | 'first';

interface Cell {
  miles: number;
  seats: number;
  direct: boolean;
}

interface Row {
  date: string;
  cabins: Partial<Record<Cabin, Cell>>;
}

interface AvailabilityBlock {
  origin: string;
  destination: string;
  program?: string;
  rows: Row[];
}

interface Selection {
  date: string;
  cabin: Cabin;
}

interface AvailabilityValue {
  picks?: Selection[];
  note?: string;
}

// economy → first, ascending quality; the last present entry is the top cabin.
const CANONICAL: readonly Cabin[] = ['economy', 'premium', 'business', 'first'];
const VISIBLE_ROWS = 10;

// "12 dates · business from 84k" — date count plus the cheapest fare in the
// highest-quality cabin present anywhere in the grid.
function summarize(rows: Row[], present: Cabin[]): string {
  const dates = `${rows.length} ${rows.length === 1 ? 'date' : 'dates'}`;
  const top = present[present.length - 1];
  if (!top) return dates;
  let min = Infinity;
  for (const row of rows) {
    const cell = row.cabins[top];
    if (cell && cell.miles < min) min = cell.miles;
  }
  return `${dates} · ${top} from ${formatMilesCompact(min)}`;
}

// A CapsLabel-styled affordance: dim at rest, text tone on hover.
function GhostButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  const t = tokens();
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...capsStyle(t),
        color: hover && !disabled ? t.text : t.dim,
        width: 'fit-content',
        background: 'transparent',
        border: 'none',
        padding: 0,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {label}
    </button>
  );
}

function CabinCell({
  cell,
  selected,
  locked,
  onToggle,
}: {
  cell: Cell | undefined;
  selected: boolean;
  locked: boolean;
  onToggle: () => void;
}) {
  const t = tokens();
  if (!cell) {
    return <div style={{ textAlign: 'center', color: t.dim, alignSelf: 'center' }}>—</div>;
  }
  return (
    <button
      type="button"
      disabled={locked}
      aria-pressed={selected}
      onClick={onToggle}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.15rem',
        padding: '0.4rem 0.3rem',
        cursor: locked ? 'not-allowed' : 'pointer',
        borderRadius: t.radiusMd,
        border: `1px solid ${selected ? t.accent : t.border}`,
        background: selected ? `color-mix(in srgb, ${t.accent} 14%, ${t.surface})` : t.surface,
        color: t.text,
        opacity: locked && !selected ? 0.55 : 1,
      }}
    >
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.2rem',
          fontFamily: t.fontMono,
          fontSize: '0.85rem',
          fontWeight: 600,
        }}
      >
        {selected && (
          <span aria-hidden style={{ color: t.accent }}>
            ✓
          </span>
        )}
        {formatMilesCompact(cell.miles)}
      </span>
      <span style={{ fontSize: '0.68rem', color: t.dim }}>
        {cell.seats} seats{cell.direct ? ' · nonstop' : ''}
      </span>
    </button>
  );
}

function Grid({
  rows,
  present,
  showHeader,
  isPicked,
  toggle,
  locked,
}: {
  rows: Row[];
  present: Cabin[];
  showHeader: boolean;
  isPicked: (date: string, cabin: Cabin) => boolean;
  toggle: (date: string, cabin: Cabin) => void;
  locked: boolean;
}) {
  const t = tokens();
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `auto repeat(${present.length}, minmax(0, 1fr))`,
        gap: '0.4rem',
        alignItems: 'stretch',
      }}
    >
      {showHeader && (
        <>
          <div />
          {present.map((c) => (
            <div
              key={c}
              style={{ fontSize: '0.7rem', textTransform: 'capitalize', color: t.dim, textAlign: 'center', padding: '0 0.25rem' }}
            >
              {c}
            </div>
          ))}
        </>
      )}
      {rows.map((row) => (
        <Fragment key={row.date}>
          <div title={row.date} style={{ alignSelf: 'center', fontSize: '0.8rem', color: t.dim, whiteSpace: 'nowrap' }}>
            {formatDateShort(row.date)}
          </div>
          {present.map((cabin) => (
            <CabinCell
              key={cabin}
              cell={row.cabins[cabin]}
              selected={isPicked(row.date, cabin)}
              locked={locked}
              onToggle={() => toggle(row.date, cabin)}
            />
          ))}
        </Fragment>
      ))}
    </div>
  );
}

export function Availability({ block, value, submit, disabled, context }: PackComponentProps) {
  const t = tokens();
  const avail = block as unknown as AvailabilityBlock;
  const val = value as AvailabilityValue | null | undefined;
  const picks = val?.picks ?? [];
  const present = CANONICAL.filter((c) => avail.rows.some((r) => r.cabins[c]));
  const locked = disabled || context.closed || context.roundOver;

  const isPicked = (date: string, cabin: Cabin) => picks.some((p) => p.date === date && p.cabin === cabin);
  // Every tap re-submits the whole merged object, so a note already on this block
  // rides beside the new picks instead of being cleared.
  const toggle = (date: string, cabin: Cabin) => {
    const next = isPicked(date, cabin)
      ? picks.filter((p) => !(p.date === date && p.cabin === cabin))
      : [...picks, { date, cabin }];
    submit({ ...(val ?? {}), picks: next });
  };
  const clear = () => submit({ ...(val ?? {}), picks: [] });

  const visible = avail.rows.slice(0, VISIBLE_ROWS);
  const overflow = avail.rows.slice(VISIBLE_ROWS);

  return (
    <div style={{ ...cardShell(t), opacity: context.closed ? 0.6 : 1 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>
            {avail.origin} → {avail.destination}
          </span>
          {avail.program && <Badge>{avail.program}</Badge>}
        </div>
        <span style={{ color: t.dim, fontSize: '0.8rem' }}>{summarize(avail.rows, present)}</span>
      </div>

      <Grid rows={visible} present={present} showHeader isPicked={isPicked} toggle={toggle} locked={locked} />

      {overflow.length > 0 && (
        <Disclosure label={`show all ${avail.rows.length} dates`}>
          <div style={{ marginTop: '0.4rem' }}>
            <Grid rows={overflow} present={present} showHeader={false} isPicked={isPicked} toggle={toggle} locked={locked} />
          </div>
        </Disclosure>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        <CapsLabel>{picks.length} picked</CapsLabel>
        {picks.length > 0 && <GhostButton label="clear" disabled={locked} onClick={clear} />}
      </div>

      <NoteBar value={value} submit={submit} disabled={disabled} context={context} />
    </div>
  );
}
