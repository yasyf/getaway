import type { CSSProperties } from 'react';
import type { PackComponentProps } from './host/present';
import type { Money } from './format';
import { formatDateShort, formatMilesCompact, formatMoney } from './format';

interface Option {
  optionId: string;
  origin: string;
  destination: string;
  date: string;
  program: string;
  miles: number;
  taxes: Money;
  cabin?: string;
}

interface OptionPickerBlock {
  label: string;
  options: Option[];
}

interface Selection {
  optionId: string;
}

const chipStyle: CSSProperties = {
  fontSize: '0.7rem',
  textTransform: 'capitalize',
  padding: '0.1rem 0.45rem',
  borderRadius: 'var(--radius-md)',
  color: 'var(--muted)',
  border: '1px solid var(--border)',
};

export function OptionPicker({ block, value, submit, disabled }: PackComponentProps) {
  const picker = block as unknown as OptionPickerBlock;
  const selected = value as Selection | null | undefined;
  const headingId = `${block.id}-label`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', color: 'var(--text)' }}>
      <div id={headingId} style={{ fontWeight: 700, fontSize: '1.05rem' }}>
        {picker.label}
      </div>
      <div role="radiogroup" aria-labelledby={headingId} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {picker.options.map((opt) => {
          const isSel = selected?.optionId === opt.optionId;
          return (
            <button
              key={opt.optionId}
              type="button"
              role="radio"
              aria-checked={isSel}
              disabled={disabled}
              onClick={() => submit({ optionId: opt.optionId })}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '1rem',
                width: '100%',
                textAlign: 'left',
                padding: '0.6rem 0.75rem',
                cursor: disabled ? 'not-allowed' : 'pointer',
                borderRadius: 'var(--radius-md)',
                border: `1px solid ${isSel ? 'var(--accent)' : 'var(--border)'}`,
                background: isSel ? 'color-mix(in srgb, var(--accent) 14%, var(--surface))' : 'var(--surface)',
                color: 'var(--text)',
                opacity: disabled && !isSel ? 0.55 : 1,
              }}
            >
              <span style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                <span style={{ fontWeight: 600 }}>
                  {opt.origin} → {opt.destination}
                </span>
                <span style={{ color: 'var(--muted)', fontSize: '0.78rem' }}>dep {formatDateShort(opt.date)}</span>
              </span>
              <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.15rem' }}>
                <span style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>{opt.program}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 600 }}>
                  {formatMilesCompact(opt.miles)} + {formatMoney(opt.taxes)}
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                  {opt.cabin && <span style={chipStyle}>{opt.cabin}</span>}
                  {isSel && <span style={{ color: 'var(--accent)', fontWeight: 700 }}>✓</span>}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
