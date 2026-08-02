/**
 * Timestamps are UTC everywhere internally and converted for display only.
 */

export function formatDateTime(iso: string | null | undefined, timezone: string): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  try {
    return new Intl.DateTimeFormat('en-NZ', {
      timeZone: timezone,
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date);
  } catch {
    // An invalid timezone in settings must not blank the whole screen.
    return date.toISOString().replace('T', ' ').slice(0, 16);
  }
}

export function formatDate(iso: string | null | undefined, timezone: string): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  try {
    return new Intl.DateTimeFormat('en-NZ', { timeZone: timezone, dateStyle: 'medium' }).format(
      date,
    );
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

/** "4 minutes ago", "just now" — for rate ages. */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'unknown';
  if (seconds < 45) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

/** Whole days from now until an ISO date, negative once it has passed. */
export function daysUntil(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return null;
  const millisecondsPerDay = 86_400_000;
  return Math.ceil((target.getTime() - Date.now()) / millisecondsPerDay);
}
