import type { CSSProperties, ReactNode } from 'react';
import type { ThemeTokens } from './host/present';
import { tokens, usePackState } from './host/present';

export type Tone = 'ok' | 'warn' | 'danger' | 'neutral';
export type SeatVerdict = 'suite' | 'solid' | 'dated' | 'barely' | 'verify';

const VERDICT_TONE: Record<SeatVerdict, Tone> = {
  suite: 'ok',
  solid: 'ok',
  dated: 'warn',
  barely: 'danger',
  verify: 'neutral',
};

// cardShell is the standard block shell: surface fill, hairline border, large
// radius, and the padding every block card sits inside.
export function cardShell(t: ThemeTokens): CSSProperties {
  return {
    display: 'flex',
    flexDirection: 'column',
    gap: '0.75rem',
    width: '100%',
    boxSizing: 'border-box',
    padding: '1rem',
    background: t.surface,
    color: t.text,
    border: `1px solid ${t.border}`,
    borderRadius: t.radiusLg,
  };
}

// capsStyle is the Blue Pencil caps label: mono, small, uppercase, tracked, dim.
export function capsStyle(t: ThemeTokens): CSSProperties {
  return {
    fontFamily: t.fontMono,
    fontSize: '0.7rem',
    letterSpacing: t.trackCaps,
    textTransform: 'uppercase',
    color: t.dim,
  };
}

function toneColor(t: ThemeTokens, tone: Tone): string {
  return tone === 'ok' ? t.ok : tone === 'warn' ? t.warn : tone === 'danger' ? t.danger : t.dim;
}

export function CapsLabel({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  const t = tokens();
  return <span style={{ ...capsStyle(t), ...style }}>{children}</span>;
}

export function Badge({ children }: { children: ReactNode }) {
  const t = tokens();
  return (
    <span
      style={{
        fontFamily: t.fontMono,
        fontSize: '0.7rem',
        textTransform: 'uppercase',
        letterSpacing: t.trackCaps,
        padding: '0.1rem 0.5rem',
        borderRadius: '999px',
        color: t.accent,
        background: `color-mix(in srgb, ${t.accent} 12%, ${t.surface})`,
        border: `1px solid color-mix(in srgb, ${t.accent} 30%, ${t.border})`,
      }}
    >
      {children}
    </span>
  );
}

// Chip is an outline chip: hairline border, tone-colored text, no fill. Neutral
// is dim text on a plain border.
export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  const t = tokens();
  const c = toneColor(t, tone);
  return (
    <span
      style={{
        fontSize: '0.7rem',
        textTransform: 'capitalize',
        padding: '0.1rem 0.45rem',
        borderRadius: t.radiusMd,
        color: c,
        border: `1px solid ${tone === 'neutral' ? t.border : `color-mix(in srgb, ${c} 45%, ${t.border})`}`,
      }}
    >
      {children}
    </span>
  );
}

// ToneChip is a filled tone pill: mono caps, a subtle tone-tinted fill, and a
// tone border. Neutral maps to dim.
export function ToneChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  const t = tokens();
  const c = toneColor(t, tone);
  return (
    <span
      style={{
        fontFamily: t.fontMono,
        fontSize: '0.68rem',
        textTransform: 'uppercase',
        letterSpacing: t.trackCaps,
        padding: '0.1rem 0.45rem',
        borderRadius: '999px',
        color: c,
        background: `color-mix(in srgb, ${c} 14%, ${t.surface})`,
        border: `1px solid color-mix(in srgb, ${c} 45%, ${t.border})`,
      }}
    >
      {children}
    </span>
  );
}

// SeatVerdictChip renders a seat-quality verdict as a ToneChip whose label is
// the verdict word itself.
export function SeatVerdictChip({ verdict }: { verdict: SeatVerdict }) {
  return <ToneChip tone={VERDICT_TONE[verdict]}>{verdict}</ToneChip>;
}

// LinkButton opens href in a new tab. primary is a filled accent button;
// secondary is a plain accent text link.
export function LinkButton({ href, children, primary }: { href: string; children: ReactNode; primary?: boolean }) {
  const t = tokens();
  const base: CSSProperties = {
    fontFamily: t.fontProse,
    fontSize: '0.85rem',
    textDecoration: 'none',
    cursor: 'pointer',
  };
  const style: CSSProperties = primary
    ? {
        ...base,
        display: 'inline-block',
        padding: '0.5rem 0.9rem',
        borderRadius: t.radiusMd,
        background: t.accent,
        color: t.accentFg,
        border: `1px solid ${t.accent}`,
      }
    : { ...base, color: t.accent };
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" style={style}>
      {children}
    </a>
  );
}

// Disclosure hides children behind a CapsLabel toggle. The chevron is a single
// glyph rotated 180deg when open — the only animation the host exposes.
export function Disclosure({
  label,
  children,
  defaultOpen,
}: {
  label: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const t = tokens();
  const [open, setOpen] = usePackState<boolean>('open', defaultOpen ?? false);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        style={{
          ...capsStyle(t),
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.3rem',
          width: 'fit-content',
          background: 'transparent',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
        }}
      >
        {open ? 'hide' : label}
        <span
          aria-hidden
          style={{
            display: 'inline-block',
            transition: 'transform 120ms ease',
            transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
          }}
        >
          ▾
        </span>
      </button>
      {open && <div>{children}</div>}
    </div>
  );
}
