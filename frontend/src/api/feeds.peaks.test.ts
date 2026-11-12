import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { getEpisodePeaks } from './feeds';

const response = (status: number, headers: Record<string, string>, body: unknown) => ({
  ok: status < 300,
  status,
  headers: { get: (n: string) => ({ 'content-type': 'application/json', ...headers })[n.toLowerCase() as 'content-type'] ?? null },
  json: async () => body,
}) as unknown as Response;
const busy = () => response(503, { 'retry-after': '30' }, { error: 'Waveform is being prepared' });

describe('getEpisodePeaks', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('waits out a 503 with Retry-After and returns the peaks once ready', async () => {
    const ready = { episodeId: 'e1', start: 0, end: 100, resolutionMs: 50, peaks: [0.5] };
    const fetchMock = vi.fn().mockResolvedValueOnce(busy()).mockResolvedValueOnce(response(200, {}, ready));
    vi.stubGlobal('fetch', fetchMock);
    const promise = getEpisodePeaks('s', 'e1', 0, 100);
    await vi.advanceTimersByTimeAsync(29_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(await promise).toEqual(ready);
  });

  it('gives up after 10 minutes of busy responses and throws the last 503', async () => {
    const fetchMock = vi.fn().mockResolvedValue(busy());
    vi.stubGlobal('fetch', fetchMock);
    const promise = getEpisodePeaks('s', 'e1', 0, 100).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + 1);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(21);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).retryAfterMs).toBe(30000);
  });
});
