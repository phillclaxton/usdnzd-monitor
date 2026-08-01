import { describe, expect, it } from 'vitest';

import {
  compareDecimal,
  formatDecimal,
  formatMoney,
  formatQuote,
  formatRate,
  roundTo,
} from './decimal';

describe('roundTo', () => {
  it('pads when fewer places are present', () => {
    expect(roundTo('1.7', 4)).toBe('1.7000');
  });

  it('rounds half away from zero', () => {
    expect(roundTo('1.75005', 4)).toBe('1.7501');
    expect(roundTo('1.75004', 4)).toBe('1.7500');
    expect(roundTo('-1.75005', 4)).toBe('-1.7501');
  });

  it('carries across the decimal point', () => {
    expect(roundTo('9.9999', 3)).toBe('10.000');
    expect(roundTo('999.995', 2)).toBe('1000.00');
  });

  it('drops the sign when the result is zero', () => {
    expect(roundTo('-0.0004', 2)).toBe('0.00');
  });
});

describe('formatDecimal', () => {
  it('groups thousands', () => {
    expect(formatDecimal('1409600.0000')).toBe('1,409,600.00');
    expect(formatDecimal('800000', { places: 0 })).toBe('800,000');
  });

  it('keeps exactness that a float round-trip would lose', () => {
    // 12345678901234.5678 is not representable in float64.
    expect(formatDecimal('12345678901234.5678', { places: 4 })).toBe('12,345,678,901,234.5678');
  });

  it('renders a fallback for missing values', () => {
    expect(formatDecimal(null)).toBe('—');
    expect(formatDecimal(undefined)).toBe('—');
    expect(formatDecimal('not a number')).toBe('—');
  });

  it('adds an explicit sign for deltas', () => {
    expect(formatDecimal('0.0042', { places: 4, signed: true })).toBe('+0.0042');
    expect(formatDecimal('-0.0042', { places: 4, signed: true })).toBe('-0.0042');
    expect(formatDecimal('0', { places: 4, signed: true })).toBe('0.0000');
  });
});

describe('currency and rate helpers', () => {
  it('labels amounts with their currency', () => {
    expect(formatMoney('1409600.0000', 'NZD')).toBe('NZD 1,409,600.00');
    expect(formatMoney(null, 'NZD')).toBe('—');
  });

  it('formats a rate without grouping', () => {
    expect(formatRate('1.76000000')).toBe('1.7600');
  });

  it('uses the product quote convention', () => {
    expect(formatQuote('1.75000000', 'USD', 'NZD')).toBe('1 USD = 1.7500 NZD');
  });
});

describe('compareDecimal', () => {
  it('orders by magnitude, not string length', () => {
    expect(compareDecimal('9', '10')).toBe(-1);
    expect(compareDecimal('1.7600', '1.76')).toBe(0);
    expect(compareDecimal('-1.76', '-1.75')).toBe(-1);
    expect(compareDecimal('1.7601', '1.76')).toBe(1);
  });
});
