/**
 * Helpers for storing and retrieving the post-login redirect destination. React Router prepends the basename
 * (UI_BASE) on navigate(), so it is stripped before storing and rejected on read; otherwise every bounce
 * stores a deeper basename/basename/... path and the loop grows unboundedly.
 */

import { UI_BASE } from '../urlPrefix';

const STORAGE_KEY = 'loginRedirect';

/** Strip every leading basename segment from a path. Handles corrupted paths. */
function stripBasename(path: string): string {
  let result = path;
  while (result.startsWith(UI_BASE + '/') || result === UI_BASE) {
    result = result.slice(UI_BASE.length) || '/';
  }
  return result || '/';
}

/**
 * Store the current location (pathname + search) as the post-login redirect, stripping the basename so
 * navigate() does not re-prepend it. Pass window.location.pathname (+ search) from the call site.
 */
export function storeLoginRedirect(pathname: string, search = ''): void {
  const stripped = stripBasename(pathname);
  const value = stripped + (search && search !== '?' ? search : '');
  sessionStorage.setItem(STORAGE_KEY, value);
}

/**
 * Read and clear the stored redirect, applying safety checks. Returns '/' when the value is absent, empty,
 * an absolute URL, a protocol-relative URL, or '/login' itself.
 */
export function takeLoginRedirect(): string {
  const raw = sessionStorage.getItem(STORAGE_KEY) ?? '';
  sessionStorage.removeItem(STORAGE_KEY);

  if (!raw) return '/';
  // Reject absolute and protocol-relative URLs.
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return '/';
  if (raw.startsWith('//')) return '/';
  // Must start with a single '/'.
  if (!raw.startsWith('/')) return '/';
  // Strip any residual basename (defensive; should not happen with storeLoginRedirect).
  const clean = stripBasename(raw);
  // Reject redirect back to the login page itself.
  if (clean === '/login' || clean.startsWith('/login?') || clean.startsWith('/login/')) return '/';
  return clean;
}
