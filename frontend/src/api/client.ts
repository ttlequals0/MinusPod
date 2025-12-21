const API_BASE = '/api/v1';

interface RequestOptions {
  method?: string;
  body?: unknown;
  skipAuthRedirect?: boolean;
}

export async function apiRequest<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, skipAuthRedirect = false } = options;

  const headers: HeadersInit = {};
  if (body) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
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

  return response.json();
}
