const API_BASE = '/api/v1';

const RETRY_DELAYS = [1000, 3000]; // 2 retries with 1s and 3s backoff

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
}

function isRetryable(status: number): boolean {
  return status >= 500 || status === 429;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(signal.reason);
    }, { once: true });
  });
}

export async function apiRequest<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, skipAuthRedirect = false, signal, skipRetry = false } = options;
  const maxAttempts = skipRetry ? 1 : RETRY_DELAYS.length + 1;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const headers: HeadersInit = {};
      if (body) {
        headers['Content-Type'] = 'application/json';
      }

      const response = await fetch(`${API_BASE}${endpoint}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal,
      });

      // Handle 401 Unauthorized - redirect to login
      if (response.status === 401 && !skipAuthRedirect) {
        const currentPath = window.location.pathname;
        if (!currentPath.includes('/login') && !endpoint.startsWith('/auth/')) {
          sessionStorage.setItem('loginRedirect', window.location.pathname);
          window.location.href = '/ui/login';
          throw new Error('Authentication required');
        }
      }

      // Retry on 5xx / 429 (not on 4xx client errors)
      if (!response.ok) {
        if (isRetryable(response.status) && attempt < maxAttempts - 1) {
          await sleep(RETRY_DELAYS[attempt], signal);
          continue;
        }
        const error = await response.json().catch(() => ({ error: 'Request failed' }));
        throw new Error(error.error || `HTTP ${response.status}`);
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
      throw err;
    }
  }

  // Unreachable: the loop always throws on the final attempt. Required for TypeScript.
  throw new Error('Request failed after retries');
}
