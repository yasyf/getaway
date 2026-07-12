// Display formatting, backed entirely by Intl — no currency or exponent tables.

export interface Money {
  amount: number;
  currency: string;
}

// Divide by the currency's own minor-unit exponent (Intl knows JPY=0, USD=2),
// so a minor-unit integer prints correctly without a per-currency table.
export function formatMoney({ amount, currency }: Money): string {
  const fmt = new Intl.NumberFormat(undefined, { style: 'currency', currency });
  const digits = fmt.resolvedOptions().maximumFractionDigits!;
  return fmt.format(amount / 10 ** digits);
}

export function formatMilesCompact(n: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n);
}

export function formatMilesExact(n: number): string {
  return new Intl.NumberFormat(undefined, { useGrouping: true }).format(n);
}

export function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}h ${String(m).padStart(2, '0')}m`;
}

// wallClock slices the digits straight out of the ISO string. A segment's Z or
// offset suffix is not semantically UTC — it is the airport's local wall clock —
// so Date + Intl would silently timezone-shift it. Never parse for display.
export function wallClock(iso: string): string {
  return iso.slice(11, 16);
}

// Eastbound dateline crossings genuinely arrive on the previous local date, so
// a negative offset ('-1') is a real state, not an error. Zero prints nothing.
export function dayOffsetSuffix(departsAt: string, arrivesAt: string): string {
  const diff = epochDay(arrivesAt) - epochDay(departsAt);
  if (diff === 0) return '';
  return diff > 0 ? `+${diff}` : String(diff);
}

// formatDateShort renders 'Mon 10/6' from a YYYY-MM-DD date. Weekday comes from
// Date.UTC on the date part only — no time, so nothing can shift across a zone.
export function formatDateShort(date: string): string {
  const mo = Number(date.slice(5, 7));
  const d = Number(date.slice(8, 10));
  const weekday = new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZone: 'UTC' }).format(
    Date.UTC(Number(date.slice(0, 4)), mo - 1, d),
  );
  return `${weekday} ${mo}/${d}`;
}

// relativeAge parses a real instant — correct for updatedAt, wrong for the
// wall-clock segment stamps, so never point it at those.
export function relativeAge(iso: string): string {
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60000);
  if (Math.abs(minutes) < 60) return rtf.format(minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return rtf.format(hours, 'hour');
  return rtf.format(Math.round(hours / 24), 'day');
}

function epochDay(iso: string): number {
  return Date.UTC(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10))) / 86400000;
}
