import { storeLoginRedirect } from '../utils/loginRedirect';
import type { JobState } from './types';
import { API_BASE, uiUrl } from '../urlPrefix';

const RETRY_DELAYS = [1000, 3000]; // 2 retries with 1s and 3s backoff

// A 503 with Retry-After means the server is preparing the answer: wait the header's time, clamped.
const BUSY_DEFAULT_MS = 5000;
const BUSY_MIN_MS = 1000;
const BUSY_MAX_MS = 30000;
const RATE_LIMIT_MAX_WAIT_MS = 5000;
const BUSY_WAIT_METHODS = new Set(['GET', 'HEAD']);

const JOB_STATES = new Set(['idle', 'submitting', 'queued', 'processing']);

const CSRF_COOKIE_NAME = 'minuspod_csrf';
const CSRF_HEADER_NAME = 'X-CSRF-Token';
const CSRF_SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const MUTATION_UNCERTAIN_MESSAGE =
  'The server did not confirm this change. It may have completed. Check the current state before trying again.';

function getCsrfToken(): string | null {
  const cookies = document.cookie.split('; ');
  for (const c of cookies) {
    const [name, ...rest] = c.split('=');
    if (name === CSRF_COOKIE_NAME) {
      return rest.join('=') || null;
    }
  }
  return null;
}

function csrfHeaders(method: string): Record<string, string> {
  if (CSRF_SAFE_METHODS.has(method.toUpperCase())) return {};
  const token = getCsrfToken();
  return token ? { [CSRF_HEADER_NAME]: token } : {};
}

export function buildQueryString(params: Record<string, string | number | boolean | undefined | null>): string {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      searchParams.set(key, String(value));
    }
  }
  const qs = searchParams.toString();
  return qs ? `?${qs}` : '';
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  skipAuthRedirect?: boolean;
  signal?: AbortSignal;
  skipRetry?: boolean;
  retry?: boolean;
  // Total time a 503 with Retry-After may keep being retried, without using up the generic retries.
  busyBudgetMs?: number;
  onResponse?: (status: number) => void;
}

function isRetryable(status: number): boolean {
  return status >= 500 || status === 429;
}

// A 429's or 503's Retry-After wins over the fixed backoff. Only the numeric-seconds form is parsed;
// a missing or non-numeric header falls back to the fixed delay.
function retryAfterMs(response: Response): number | null {
  const header = response.headers.get('Retry-After');
  if (!header) return null;
  const seconds = Number(header);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  return seconds * 1000;
}

function busyWaitMs(response: Response): number {
  return Math.min(BUSY_MAX_MS, Math.max(BUSY_MIN_MS, retryAfterMs(response) ?? BUSY_DEFAULT_MS));
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

// Turn a backend error body into a usable string so `new Error(...).message` never reads "[object Object]".
// Bodies vary: `{error: 'string'}` or `{error: {message, reasons}}` (e.g. submit-to-community 400).
function extractErrorMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object') {
    const err = (body as { error?: unknown }).error;
    if (typeof err === 'string' && err) return err;
    if (err && typeof err === 'object') {
      const msg = (err as { message?: unknown }).message;
      if (typeof msg === 'string' && msg) return msg;
      try {
        return JSON.stringify(err);
      } catch {
        // fall through to status code
      }
    }
  }
  return `HTTP ${status}`;
}

// Narrow an unknown catch/onError value to a display string. apiRequest and
// apiFileRequest always throw Error instances, so callers get err.message;
// the fallback covers non-Error throws from other code paths.
export function getErrorMessage(err: unknown, fallback = 'Request failed'): string {
  return err instanceof Error ? err.message : fallback;
}

const PROXY_413_MESSAGE =
  'The file is too large for the server or a proxy in front of it.';

// A proxy 413 has an HTML body, so a body without our JSON error string gets the proxy hint.
function errorMessageFor(body: unknown, status: number): string {
  const hasError = typeof (body as { error?: unknown } | null)?.error === 'string';
  if (status === 413 && !hasError) return PROXY_413_MESSAGE;
  return extractErrorMessage(body ?? { error: 'Request failed' }, status);
}

// Thrown by apiRequest on a non-ok response. Carries the HTTP status (so callers can branch on 404 etc.)
// and the parsed body (a 409 carries the authoritative jobState).
export class ApiError extends Error {
  status: number;
  body: unknown;
  retryAfterMs: number | null;
  constructor(message: string, status: number, body?: unknown, retryAfterMs: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.retryAfterMs = retryAfterMs;
  }

  get jobState(): JobState | undefined {
    return jobStateOf(this.body);
  }
}

// Reads a jobState off any response body (success or error), ignoring values
// the client does not know.
export function jobStateOf(body: unknown): JobState | undefined {
  if (!body || typeof body !== 'object') return undefined;
  const value = (body as { jobState?: unknown }).jobState;
  return typeof value === 'string' && JOB_STATES.has(value)
    ? value as JobState
    : undefined;
}

// Redirect to login on 401, preserving the current path. Throws so callers stop.
function handleUnauthorized(endpoint: string): void {
  const currentPath = window.location.pathname;
  if (!currentPath.includes('/login') && !endpoint.startsWith('/auth/')) {
    storeLoginRedirect(window.location.pathname, window.location.search);
    window.location.href = uiUrl('/login');
    throw new Error('Authentication required');
  }
}

export async function apiRequest<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = 'GET', body, skipAuthRedirect = false, signal,
    skipRetry = false, retry, busyBudgetMs, onResponse,
  } = options;
  const safeMethod = CSRF_SAFE_METHODS.has(method.toUpperCase());
  const retryEnabled = retry ?? (safeMethod && !skipRetry);
  const maxAttempts = retryEnabled ? RETRY_DELAYS.length + 1 : 1;
  const busyDeadline = busyBudgetMs === undefined ? undefined : Date.now() + busyBudgetMs;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      // FormData passes through untouched (mirrors apiFileRequest): no
      // Content-Type header so the browser sets the multipart boundary.
      const isFormData = body instanceof FormData;
      const headers: Record<string, string> = {};
      if (body && !isFormData) {
        headers['Content-Type'] = 'application/json';
      }
      Object.assign(headers, csrfHeaders(method));

      const response = await fetch(`${API_BASE}${endpoint}`, {
        method,
        headers,
        body: isFormData ? (body as FormData) : body ? JSON.stringify(body) : undefined,
        signal,
      });
      onResponse?.(response.status);

      // Handle 401 Unauthorized - redirect to login
      if (response.status === 401 && !skipAuthRedirect) {
        handleUnauthorized(endpoint);
      }

      // Retry on 5xx / 429 (not on 4xx client errors)
      if (!response.ok) {
        const busy = response.status === 503 && response.headers.get('Retry-After') !== null;
        if (retryEnabled && isRetryable(response.status)) {
          if (busy && busyDeadline !== undefined) {
            // Only GET and HEAD wait. A busy answer means earlier transient failures are over, so reset the attempts.
            const wait = busyWaitMs(response);
            if (BUSY_WAIT_METHODS.has(method.toUpperCase()) && Date.now() + wait <= busyDeadline) {
              await sleep(wait, signal);
              attempt = -1;
              continue;
            }
          } else if (attempt < maxAttempts - 1) {
            const headerWait = response.status === 429 ? retryAfterMs(response) : null;
            // A long rate-limit wait must not hang a user action: surface the 429 instead.
            if (headerWait === null || headerWait <= RATE_LIMIT_MAX_WAIT_MS) {
              await sleep(headerWait ?? RETRY_DELAYS[attempt], signal);
              continue;
            }
          }
        }
        const error = await response.json().catch(() => null);
        throw new ApiError(errorMessageFor(error, response.status), response.status, error ?? { error: 'Request failed' }, retryAfterMs(response));
      }

      const contentType = response.headers.get('content-type');
      if (response.status === 204 || response.headers.get('content-length') === '0' || !contentType?.includes('application/json')) {
        return {} as T;
      }
      return response.json();
    } catch (err) {
      // Don't retry aborted requests or auth redirects
      if (signal?.aborted || (err instanceof Error && err.message === 'Authentication required')) {
        throw err;
      }
      // Retry network errors (TypeError from fetch)
      if (err instanceof TypeError && attempt < maxAttempts - 1) {
        await sleep(RETRY_DELAYS[attempt], signal);
        continue;
      }
      if (err instanceof TypeError && !safeMethod) {
        const uncertain = new Error(MUTATION_UNCERTAIN_MESSAGE) as Error & { cause?: unknown };
        uncertain.cause = err;
        throw uncertain;
      }
      throw err;
    }
  }

  // Unreachable: the loop always throws on the final attempt. Required for TypeScript.
  throw new Error('Request failed after retries');
}

interface FileRequestOptions {
  method?: 'GET' | 'POST' | 'HEAD';
  body?: BodyInit | object;
  fallbackFilename?: string;
}

// Blob-download helper for endpoints that stream a file (apiRequest assumes JSON), keeping the CSRF header
// and error stringification. Returns the blob and the Content-Disposition filename (or fallbackFilename).
export async function apiFileRequest(
  endpoint: string,
  options: FileRequestOptions = {}
): Promise<{ blob: Blob; filename: string }> {
  const { method = 'GET', body, fallbackFilename = 'download' } = options;

  const headers: Record<string, string> = {};
  let serializedBody: BodyInit | undefined;
  if (body !== undefined) {
    if (
      body instanceof FormData ||
      body instanceof Blob ||
      body instanceof ArrayBuffer ||
      body instanceof URLSearchParams ||
      typeof body === 'string'
    ) {
      serializedBody = body as BodyInit;
    } else {
      headers['Content-Type'] = 'application/json';
      serializedBody = JSON.stringify(body);
    }
  }
  Object.assign(headers, csrfHeaders(method));

  const response = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers,
    body: serializedBody,
  });

  if (response.status === 401) {
    handleUnauthorized(endpoint);
  }

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(errorMessageFor(error, response.status));
  }

  const blob = await response.blob();
  const cd = response.headers.get('Content-Disposition') || '';
  const match = cd.match(/filename="?([^";]+)"?/);
  const filename = match?.[1] || fallbackFilename;
  return { blob, filename };
}
