/**
 * Small presentational building blocks.
 *
 * `Tag` exists because the specification is strict that a figure must never be
 * shown without saying whether it is gross, an estimate, or an actual result.
 */
import type { ReactNode } from 'react';

export type Quality = 'gross' | 'estimate' | 'actual' | 'warning' | 'plain';

const QUALITY_LABEL: Record<Quality, string> = {
  gross: 'Gross',
  estimate: 'Estimated',
  actual: 'Actual',
  warning: 'Check',
  plain: '',
};

export function Tag({ quality, children }: { quality: Quality; children?: ReactNode }) {
  const label = children ?? QUALITY_LABEL[quality];
  if (!label) return null;
  return <span className={`fx-tag is-${quality}`}>{label}</span>;
}

export function Card({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="fx-card">
      {(title || actions) && (
        <div className="fx-inline" style={{ justifyContent: 'space-between' }}>
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {subtitle && <p className="fx-card-subtitle">{subtitle}</p>}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  note,
  quality = 'plain',
  small = false,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  quality?: Quality;
  small?: boolean;
}) {
  return (
    <div className="fx-stat">
      <div className="fx-stat-label">
        <span>{label}</span>
        {quality !== 'plain' && <Tag quality={quality} />}
      </div>
      <div className={`fx-stat-value${small ? ' is-small' : ''}`}>{value}</div>
      {note && <div className="fx-stat-note">{note}</div>}
    </div>
  );
}

export function Banner({
  tone = 'info',
  glyph,
  children,
}: {
  tone?: 'info' | 'warning' | 'error' | 'simulation';
  glyph?: string;
  children: ReactNode;
}) {
  const defaultGlyph = { info: 'ℹ', warning: '⚠', error: '⨯', simulation: '⚗' }[tone];
  return (
    <div className={`fx-banner is-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span aria-hidden="true">{glyph ?? defaultGlyph}</span>
      <div>{children}</div>
    </div>
  );
}

export function EmptyState({
  glyph = '◍',
  title,
  children,
}: {
  glyph?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="fx-empty">
      <span className="fx-empty-glyph" aria-hidden="true">
        {glyph}
      </span>
      <p style={{ fontWeight: 600, margin: '0 0 4px' }}>{title}</p>
      {children}
    </div>
  );
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p className="fx-loading" role="status">
      {label}
    </p>
  );
}

export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="fx-field">
      <label htmlFor={htmlFor}>
        {label}
        {hint && <span className="fx-hint"> — {hint}</span>}
      </label>
      {children}
      {error && <span className="fx-error">{error}</span>}
    </div>
  );
}
