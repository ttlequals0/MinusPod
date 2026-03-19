const API_BASE = '/api/v1';

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
}

export async function apiRequest<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, skipAuthRedirect = false, signal } = options;

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
    // Don't redirect if we're already on the login page or checking auth status
    const currentPath = window.location.pathname;
    if (!currentPath.includes('/login') && !endpoint.startsWith('/auth/')) {
      // Store the current URL for redirect after login
      sessionStorage.setItem('loginRedirect', window.location.pathname);
      window.location.href = '/ui/login';
      throw new Error('Authentication required');
    }
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Request failed' }));
    throw new Error(error.error || `HTTP ${response.status}`);
  }

  const contentType = response.headers.get('content-type');
  if (response.status === 204 || response.headers.get('content-length') === '0' || !contentType?.includes('application/json')) {
    return {} as T;
  }
  return response.json();
}
