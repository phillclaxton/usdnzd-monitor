/**
 * Ingress-aware URL resolution.
 *
 * Home Assistant serves this add-on from an unpredictable prefix such as
 * `/api/hassio_ingress/<token>/`. The backend injects a matching `<base href>`
 * into index.html, so every URL the app builds is resolved against
 * `document.baseURI` rather than the origin root. Nothing here assumes '/'.
 */

/** The path the app is mounted at, always with a trailing slash. */
export function basePath(): string {
  const base = new URL(document.baseURI);
  return base.pathname.endsWith('/') ? base.pathname : `${base.pathname}/`;
}

/** Base path without the trailing slash, for React Router's `basename`. */
export function routerBasename(): string {
  const path = basePath();
  return path === '/' ? '/' : path.replace(/\/$/, '');
}

/** Resolve an app-relative path (e.g. `api/v1/health`) to an absolute URL. */
export function resolveUrl(path: string): string {
  return new URL(path.replace(/^\//, ''), document.baseURI).toString();
}

/** Resolve an app-relative path to a WebSocket URL on the same origin. */
export function resolveWebSocketUrl(path: string): string {
  const url = new URL(path.replace(/^\//, ''), document.baseURI);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}
