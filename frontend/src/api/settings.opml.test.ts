import { afterEach, describe, expect, it, vi } from 'vitest';
import { downloadBlob } from './history';
import { exportOpml } from './settings';

vi.mock('./history', () => ({ downloadBlob: vi.fn() }));

describe('exportOpml', () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    document.cookie = 'minuspod_csrf=; Max-Age=0';
  });

  it('posts explicit slugs in the JSON body with the CSRF header', async () => {
    document.cookie = 'minuspod_csrf=test-token';
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob(['<opml/>']),
      headers: new Headers({ 'Content-Disposition': 'attachment; filename="minuspod-feeds-modified.opml"' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await exportOpml('modified', ['alpha-show', 'bravo-show']);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/feeds/export-opml?mode=modified');
    expect(options.method).toBe('POST');
    expect(options.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'test-token',
    });
    expect(options.body).toBe(JSON.stringify({ slugs: ['alpha-show', 'bravo-show'] }));
    expect(downloadBlob).toHaveBeenCalledOnce();
  });

  it('keeps the all-feed export as a GET request', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob(['<opml/>']),
      headers: new Headers(),
    });
    vi.stubGlobal('fetch', fetchMock);

    await exportOpml('original');

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/feeds/export-opml?mode=original');
    expect(options.method).toBe('GET');
    expect(options.body).toBeUndefined();
  });
});
