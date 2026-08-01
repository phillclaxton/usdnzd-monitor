import { beforeEach, describe, expect, it } from 'vitest';

import { basePath, resolveUrl, resolveWebSocketUrl, routerBasename } from './basePath';

const INGRESS = '/api/hassio_ingress/8CmaXHCoP0dNJcvbeqYKmA/';

function setBaseHref(href: string) {
  document.head.querySelectorAll('base').forEach((tag) => tag.remove());
  const base = document.createElement('base');
  base.setAttribute('href', href);
  document.head.appendChild(base);
}

describe('ingress base path resolution', () => {
  beforeEach(() => {
    document.head.querySelectorAll('base').forEach((tag) => tag.remove());
  });

  it('falls back to the root when no base tag is present', () => {
    expect(basePath()).toBe('/');
    expect(routerBasename()).toBe('/');
  });

  it('reads the ingress prefix from the injected base tag', () => {
    setBaseHref(INGRESS);
    expect(basePath()).toBe(INGRESS);
    expect(routerBasename()).toBe(INGRESS.replace(/\/$/, ''));
  });

  it('builds API URLs under the ingress prefix', () => {
    setBaseHref(INGRESS);
    expect(resolveUrl('api/v1/health')).toBe(`http://localhost:3000${INGRESS}api/v1/health`);
    // A leading slash must not escape the prefix.
    expect(resolveUrl('/api/v1/health')).toBe(`http://localhost:3000${INGRESS}api/v1/health`);
  });

  it('builds WebSocket URLs on the same prefix', () => {
    setBaseHref(INGRESS);
    expect(resolveWebSocketUrl('api/v1/events')).toBe(
      `ws://localhost:3000${INGRESS}api/v1/events`,
    );
  });
});
