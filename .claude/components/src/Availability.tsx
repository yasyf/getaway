import { Fragment } from 'react';
import type { CSSProperties } from 'react';
import type { PackComponentProps } from './host/present';
import { formatDateShort, formatMilesCompact } from './format';

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

const CANONICAL: readonly Cabin[] = ['economy', 'premium', 'business', 'first'];

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

const headerCellStyle: CSSProperties = {
  fontSize: '0.7rem',
  textTransform: 'capitalize',
  color: 'var(--muted)',
  textAlign: 'center',
  padding: '0 0.25rem',
};

export function Availability({ block, value, submit, disabled }: PackComponentProps) {
  const avail = block as unknown as AvailabilityBlock;
  const selected = value as Selection | null | undefined;
  const present = CANONICAL.filter((c) => avail.rows.some((r) => r.cabins[c]));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', color: 'var(--text)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>
          {avail.origin} → {avail.destination}
        </span>
        {avail.program && <span style={badgeStyle}>{avail.program}</span>}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `auto repeat(${present.length}, minmax(0, 1fr))`,
          gap: '0.4rem',
          alignItems: 'stretch',
        }}
      >
        <div />
        {present.map((c) => (
          <div key={c} style={headerCellStyle}>
            {c}
          </div>
        ))}

        {avail.rows.map((row) => (
          <Fragment key={row.date}>
            <div
              title={row.date}
              style={{ alignSelf: 'center', fontSize: '0.8rem', color: 'var(--muted)', whiteSpace: 'nowrap' }}
            >
              {formatDateShort(row.date)}
            </div>
            {present.map((cabin) => {
              const cell = row.cabins[cabin];
              if (!cell) {
                return (
                  <div key={cabin} style={{ textAlign: 'center', color: 'var(--muted)', alignSelf: 'center' }}>
                    —
                  </div>
                );
              }
              const isSel = selected?.date === row.date && selected?.cabin === cabin;
              return (
                <button
                  key={cabin}
                  type="button"
                  disabled={disabled}
                  aria-pressed={isSel}
                  onClick={() => submit({ date: row.date, cabin })}
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.15rem',
                    padding: '0.4rem 0.3rem',
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    borderRadius: 'var(--radius-md)',
                    border: `1px solid ${isSel ? 'var(--accent)' : 'var(--border)'}`,
                    background: isSel ? 'color-mix(in srgb, var(--accent) 14%, var(--surface))' : 'var(--surface)',
                    color: 'var(--text)',
                    opacity: disabled && !isSel ? 0.55 : 1,
                  }}
                >
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 600 }}>
                    {formatMilesCompact(cell.miles)}
                  </span>
                  <span style={{ fontSize: '0.68rem', color: 'var(--muted)' }}>
                    {cell.seats} seats{cell.direct ? ' · nonstop' : ''}
                  </span>
                </button>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
