/**
 * loginRedirect (#460): the '/ui' basename is stripped from stored paths and invalid values are rejected,
 * preventing the /ui/ui/ui growth loop.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { storeLoginRedirect, takeLoginRedirect } from './loginRedirect';

// happy-dom provides sessionStorage; reset between tests.
beforeEach(() => {
  sessionStorage.clear();
});

describe('storeLoginRedirect', () => {
  it('strips /ui basename before storing', () => {
    // Simulate window.location.pathname = '/ui/feeds'
    storeLoginRedirect('/ui/feeds');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/feeds');
  });

  it('self-heals a deeply-nested corrupted path', () => {
    storeLoginRedirect('/ui/ui/ui/feeds');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/feeds');
  });

  it('stores root correctly', () => {
    storeLoginRedirect('/ui/');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/');
  });

  it('preserves query string after stripping basename', () => {
    storeLoginRedirect('/ui/feeds?sort=newest');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/feeds?sort=newest');
  });
});

describe('login redirect hash', () => {
  it('round-trips path, query and hash', () => {
    storeLoginRedirect('/ui/feeds/x', '?section=ads', '#ad-distribution-section');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/feeds/x?section=ads#ad-distribution-section');
    expect(takeLoginRedirect()).toBe('/feeds/x?section=ads#ad-distribution-section');
  });

  it('keeps a hash with no query', () => {
    storeLoginRedirect('/ui/settings', '', '#api-tokens');
    expect(takeLoginRedirect()).toBe('/settings#api-tokens');
  });

  it('ignores a bare # or ?', () => {
    storeLoginRedirect('/ui/settings', '?', '#');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/settings');
  });

  it('rejects /login#x and keeps a hash that looks like a URL as a hash only', () => {
    sessionStorage.setItem('loginRedirect', '/login#x');
    expect(takeLoginRedirect()).toBe('/');
    sessionStorage.setItem('loginRedirect', '/settings#//evil.example.com');
    expect(takeLoginRedirect()).toBe('/settings#//evil.example.com');
  });
});

describe('takeLoginRedirect', () => {
  it('returns stored path and clears the key', () => {
    sessionStorage.setItem('loginRedirect', '/feeds');
    const result = takeLoginRedirect();
    expect(result).toBe('/feeds');
    expect(sessionStorage.getItem('loginRedirect')).toBeNull();
  });

  it('falls back to / when nothing is stored', () => {
    expect(takeLoginRedirect()).toBe('/');
  });

  it('rejects protocol-relative URLs (//evil)', () => {
    sessionStorage.setItem('loginRedirect', '//evil.example.com');
    expect(takeLoginRedirect()).toBe('/');
  });

  it('rejects absolute http URLs', () => {
    sessionStorage.setItem('loginRedirect', 'http://evil.example.com');
    expect(takeLoginRedirect()).toBe('/');
  });

  it('rejects /login to avoid redirect to the login page itself', () => {
    sessionStorage.setItem('loginRedirect', '/login');
    expect(takeLoginRedirect()).toBe('/');
  });

  it('strips any residual /ui prefix from a stored value', () => {
    sessionStorage.setItem('loginRedirect', '/ui/feeds');
    expect(takeLoginRedirect()).toBe('/feeds');
  });

  it('returns / for empty stored string', () => {
    sessionStorage.setItem('loginRedirect', '');
    expect(takeLoginRedirect()).toBe('/');
  });
});

describe('round-trip: navigate() with basename no longer doubles /ui', () => {
  // React Router strips basename '/ui' before navigate(), so storing '/feeds' lands on /ui/feeds.
  it('stored path + basename does not produce /ui/ui', () => {
    // Store a path as if coming from window.location.pathname='/ui/feeds'
    storeLoginRedirect('/ui/feeds');
    const target = takeLoginRedirect(); // should be '/feeds'
    // React Router navigate(target) renders to basename+target = '/ui' + '/feeds' = '/ui/feeds'
    // NOT '/ui/ui/feeds'
    expect(target).toBe('/feeds');
    expect(target.startsWith('/ui')).toBe(false);
  });
});

describe('with a URL prefix', () => {
  afterEach(() => {
    document.head.querySelectorAll('meta[name="minuspod-url-prefix"]').forEach(m => m.remove());
    vi.resetModules();
  });

  it('strips the prefixed basename before storing and on read', async () => {
    const meta = document.createElement('meta');
    meta.name = 'minuspod-url-prefix';
    meta.content = '/minuspod';
    document.head.appendChild(meta);
    vi.resetModules();
    const { storeLoginRedirect: store, takeLoginRedirect: take } = await import('./loginRedirect');
    store('/minuspod/ui/feeds', '?tab=1');
    expect(sessionStorage.getItem('loginRedirect')).toBe('/feeds?tab=1');
    sessionStorage.setItem('loginRedirect', '/minuspod/ui/minuspod/ui/feeds');
    expect(take()).toBe('/feeds');
  });
});
