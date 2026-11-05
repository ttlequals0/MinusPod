import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

function setMeta(content: string | null) {
  document.head.querySelectorAll('meta[name="minuspod-url-prefix"]').forEach(m => m.remove());
  if (content === null) return;
  const meta = document.createElement('meta');
  meta.name = 'minuspod-url-prefix';
  meta.content = content;
  document.head.appendChild(meta);
}

async function load() {
  vi.resetModules();
  return import('./urlPrefix');
}

beforeEach(() => setMeta(null));
afterEach(() => setMeta(null));

describe('urlPrefix', () => {
  it('is empty and leaves the base paths unchanged without the meta tag', async () => {
    const m = await load();
    expect(m.URL_PREFIX).toBe('');
    expect(m.UI_BASE).toBe('/ui');
    expect(m.API_BASE).toBe('/api/v1');
    expect(m.uiUrl('/login')).toBe('/ui/login');
    expect(m.apiUrl('/feeds')).toBe('/api/v1/feeds');
  });

  it('prefixes every base path from the meta tag', async () => {
    setMeta('/minuspod');
    const m = await load();
    expect(m.URL_PREFIX).toBe('/minuspod');
    expect(m.UI_BASE).toBe('/minuspod/ui');
    expect(m.API_BASE).toBe('/minuspod/api/v1');
    expect(m.uiUrl('/login')).toBe('/minuspod/ui/login');
    expect(m.apiUrl('/feeds')).toBe('/minuspod/api/v1/feeds');
  });
});
