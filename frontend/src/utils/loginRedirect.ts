/**
 * Helpers for storing and retrieving the post-login redirect destination.
 *
 * React Router prepends the basename ('/ui') when navigate() is called, so we
 * must strip it before storing and reject it on read. Without this, every
 * bounce stores the deeper /ui/ui/... path and the loop grows unboundedly.
 */

import { UI_BASE } from '../urlPrefix';

const STORAGE_KEY = 'loginRedirect';
const BASENAME = UI_BASE;

/** Strip every leading '/ui' segment from a path. Handles corrupted paths. */
function stripBasename(path: string): string {
  let result = path;
  while (result.startsWith(BASENAME + '/') || result === BASENAME) {
    result = result.slice(BASENAME.length) || '/';
  }
  return result || '/';
}

/**
 * Store the current location (pathname + search) as the post-login redirect
 * destination. Strips the '/ui' basename so navigate() does not re-prepend it.
 *
 * Pass window.location.pathname (+ search) from the call site; callers in
 * GlobalStatusBar and client.ts already have the full browser path.
 */
export function storeLoginRedirect(pathname: string, search = ''): void {
  const stripped = stripBasename(pathname);
  const value = stripped + (search && search !== '?' ? search : '');
  sessionStorage.setItem(STORAGE_KEY, value);
}

/**
 * Read and clear the stored redirect destination, applying safety checks.
 * Returns '/' when the stored value is absent, empty, an absolute URL,
 * a protocol-relative URL, or '/login' itself.
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
