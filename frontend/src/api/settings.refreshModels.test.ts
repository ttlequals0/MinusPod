import { describe, it, expect, vi, afterEach } from 'vitest';
import { refreshModels } from './settings';

function okResponse() {
  return vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ models: [], count: 0 }), { status: 200 }));
}

describe('refreshModels slot', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('names the primary slot whether or not the caller passes one', async () => {
    const fetchMock = okResponse();
    vi.stubGlobal('fetch', fetchMock);

    await refreshModels();
    await refreshModels('primary');

    for (const call of fetchMock.mock.calls) {
      expect(call[0]).toBe('/api/v1/settings/models/refresh');
      expect(call[1].method).toBe('POST');
      expect(JSON.parse(call[1].body)).toEqual({ slot: 'primary' });
    }
  });

  it('names the secondary slot in the body', async () => {
    const fetchMock = okResponse();
    vi.stubGlobal('fetch', fetchMock);

    await refreshModels('secondary');

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ slot: 'secondary' });
  });

  it('surfaces the endpoint error for an unconfigured secondary provider', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: 'No secondary provider configured', status: 400 }),
      { status: 400 })));

    await expect(refreshModels('secondary')).rejects.toThrow('No secondary provider configured');
  });
});
