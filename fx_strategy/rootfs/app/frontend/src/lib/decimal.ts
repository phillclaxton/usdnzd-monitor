/**
 * String-based decimal handling.
 *
 * The backend sends money and rates as exact decimal strings. Converting them
 * to JavaScript numbers to display them would round through binary floating
 * point — at NZD 1,409,600.00 that is invisible, but it is exactly the class of
 * error this application is built to avoid. Everything here works on the digit
 * strings directly.
 */

export interface DecimalParts {
  negative: boolean;
  integer: string;
  fraction: string;
}

const DECIMAL_RE = /^-?\d+(\.\d+)?$/;

export function isDecimalString(value: string): boolean {
  return DECIMAL_RE.test(value.trim());
}

export function parseParts(value: string): DecimalParts | null {
  const trimmed = value.trim();
  if (!isDecimalString(trimmed)) return null;
  const negative = trimmed.startsWith('-');
  const unsigned = negative ? trimmed.slice(1) : trimmed;
  const [integer = '0', fraction = ''] = unsigned.split('.');
  return { negative, integer: integer.replace(/^0+(?=\d)/, ''), fraction };
}

/** Round a decimal string to `places`, half away from zero. */
export function roundTo(value: string, places: number): string {
  const parts = parseParts(value);
  if (!parts) return value;
  const { negative, integer, fraction } = parts;

  if (fraction.length <= places) {
    return `${negative ? '-' : ''}${integer}.${fraction.padEnd(places, '0')}`.replace(/\.$/, '');
  }

  const keep = fraction.slice(0, places);
  const nextDigit = Number(fraction[places] ?? '0');
  let digits = `${integer}${keep}`;
  if (nextDigit >= 5) {
    // Increment the digit string by one without ever creating a number.
    const chars = digits.split('');
    let index = chars.length - 1;
    for (;;) {
      if (index < 0) {
        chars.unshift('1');
        break;
      }
      const incremented = Number(chars[index]) + 1;
      if (incremented < 10) {
        chars[index] = String(incremented);
        break;
      }
      chars[index] = '0';
      index -= 1;
    }
    digits = chars.join('');
  }

  const cut = digits.length - places;
  const newInteger = (cut > 0 ? digits.slice(0, cut) : '0').replace(/^0+(?=\d)/, '') || '0';
  const newFraction = places > 0 ? digits.slice(Math.max(cut, 0)).padStart(places, '0') : '';
  const sign = negative && !(newInteger === '0' && /^0*$/.test(newFraction)) ? '-' : '';
  return places > 0 ? `${sign}${newInteger}.${newFraction}` : `${sign}${newInteger}`;
}

function group(integer: string, separator: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, separator);
}

export interface FormatOptions {
  places?: number;
  grouping?: boolean;
  /** Show an explicit `+` for positive values, useful for deltas. */
  signed?: boolean;
  /** Rendered when the value is null/undefined/unparsable. */
  fallback?: string;
}

export function formatDecimal(
  value: string | null | undefined,
  { places = 2, grouping = true, signed = false, fallback = '—' }: FormatOptions = {},
): string {
  if (value === null || value === undefined || value === '') return fallback;
  const rounded = roundTo(String(value), places);
  const parts = parseParts(rounded);
  if (!parts) return fallback;
  const integer = grouping ? group(parts.integer, ',') : parts.integer;
  const body = places > 0 ? `${integer}.${parts.fraction.padEnd(places, '0')}` : integer;
  if (parts.negative) return `-${body}`;
  return signed && !/^0(\.0*)?$/.test(rounded) ? `+${body}` : body;
}

/** Format an amount with its currency code, e.g. `NZD 1,409,600.00`. */
export function formatMoney(
  value: string | null | undefined,
  currency: string,
  options: FormatOptions = {},
): string {
  const formatted = formatDecimal(value, { places: 2, ...options });
  return formatted === (options.fallback ?? '—') ? formatted : `${currency} ${formatted}`;
}

/** Format an exchange rate, four places by default. */
export function formatRate(value: string | null | undefined, places = 4): string {
  return formatDecimal(value, { places, grouping: false });
}

/** Render the quote in the convention the product uses: `1 USD = 1.7500 NZD`. */
export function formatQuote(
  rate: string | null | undefined,
  source: string,
  target: string,
  places = 4,
): string {
  const formatted = formatRate(rate, places);
  return formatted === '—' ? '—' : `1 ${source} = ${formatted} ${target}`;
}

/** Compare two decimal strings without converting to Number. */
export function compareDecimal(a: string, b: string): number {
  const left = parseParts(a);
  const right = parseParts(b);
  if (!left || !right) return 0;
  if (left.negative !== right.negative) return left.negative ? -1 : 1;
  const scale = Math.max(left.fraction.length, right.fraction.length);
  const leftDigits = `${left.integer}${left.fraction.padEnd(scale, '0')}`;
  const rightDigits = `${right.integer}${right.fraction.padEnd(scale, '0')}`;
  const padded = Math.max(leftDigits.length, rightDigits.length);
  const l = leftDigits.padStart(padded, '0');
  const r = rightDigits.padStart(padded, '0');
  const magnitude = l === r ? 0 : l < r ? -1 : 1;
  return left.negative ? -magnitude : magnitude;
}

/**
 * Convert to a Number. Only for chart pixel positions and progress bars, never
 * for a displayed figure — call sites are expected to be obvious about it.
 */
export function toChartNumber(value: string | null | undefined): number {
  if (value === null || value === undefined || value === '') return Number.NaN;
  return Number(value);
}
