import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { getEpisodePeaks } from './feeds';

describe('getEpisodePeaks', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('hands a 503 with Retry-After to the caller after a single request', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      headers: { get: (n: string) => ({ 'retry-after': '30', 'content-type': 'application/json' })[n.toLowerCase()] ?? null },
      json: async () => ({ error: 'Waveform is being prepared' }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);
    const error = await getEpisodePeaks('s', 'e1', 0, 100).catch((e: unknown) => e);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).retryAfterMs).toBe(30000);
  });
});
