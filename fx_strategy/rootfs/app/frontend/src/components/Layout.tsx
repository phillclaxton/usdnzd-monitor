import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

import { useHealth, useSettings } from '@/hooks/useSettings';
import { Banner } from './ui';

const NAV = [
  { to: '/', label: 'Dashboard', glyph: '◎', end: true },
  { to: '/chart', label: 'Rate chart', glyph: '📈' },
  { to: '/strategy', label: 'Strategy', glyph: '🪜' },
  { to: '/conversions', label: 'Conversions', glyph: '💱' },
  { to: '/settings', label: 'Settings', glyph: '⚙' },
];

export default function Layout({ children }: { children: ReactNode }) {
  const health = useHealth();
  const settings = useSettings();
  const simulation = settings.data?.simulation.enabled ?? health.data?.simulation_mode ?? false;

  return (
    <div className="fx-shell">
      <a className="fx-skip-link" href="#fx-main">
        Skip to content
      </a>
      <header className="fx-header">
        <h1>FX Strategy Manager</h1>
        <div className="fx-header-meta">
          <div>{health.data ? `v${health.data.version}` : 'connecting…'}</div>
          <div>
            {health.data?.database === 'ok'
              ? 'Database OK'
              : health.isError
                ? 'Backend unreachable'
                : 'Checking…'}
          </div>
        </div>
      </header>

      <nav className="fx-nav" aria-label="Sections">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end}>
            <span className="fx-nav-glyph" aria-hidden="true">
              {item.glyph}
            </span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <main className="fx-main" id="fx-main">
        {simulation && (
          <Banner tone="simulation">
            SIMULATION MODE: No live financial decisions should be based on this screen.
          </Banner>
        )}
        {health.isError && (
          <Banner tone="error">
            The backend is not responding. Displayed figures may be out of date.
          </Banner>
        )}
        {children}
      </main>
    </div>
  );
}
