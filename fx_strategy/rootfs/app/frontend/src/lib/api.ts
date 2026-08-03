/**
 * Thin fetch wrapper for the versioned internal API.
 *
 * Money and rates arrive as strings and stay strings all the way to the
 * formatter. Parsing them into JavaScript numbers would reintroduce the binary
 * rounding the backend works to avoid, so this module never calls Number() on
 * a financial field.
 */
import { resolveUrl } from './basePath';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string; details?: unknown };
  detail?: unknown;
}

async function toError(response: Response): Promise<ApiError> {
  let code = 'http_error';
  let message = `${response.status} ${response.statusText}`;
  let details: unknown;
  try {
    const body = (await response.json()) as ErrorBody;
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    } else if (body.detail) {
      code = 'validation_error';
      message = 'The submitted values were rejected.';
      details = body.detail;
    }
  } catch {
    // A non-JSON error body is reported as-is rather than hidden.
  }
  return new ApiError(response.status, code, message, details);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(resolveUrl(`api/v1/${path.replace(/^\//, '')}`), {
    credentials: 'same-origin',
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) return (await response.text()) as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { method: 'GET', signal }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  /**
   * A partial edit. A key present with a null value clears that field; a key
   * left out is not touched. JSON.stringify keeps explicit nulls, which is what
   * makes "remove this due date" expressible at all.
   */
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** Absolute URL for downloads and file uploads that bypass JSON handling. */
  url: (path: string) => resolveUrl(`api/v1/${path.replace(/^\//, '')}`),
};
