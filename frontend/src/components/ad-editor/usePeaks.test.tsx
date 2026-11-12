import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import { usePeaks } from './usePeaks';

const mockGetPeaks = vi.fn();
vi.mock('../../api/feeds', () => ({
  getEpisodePeaks: (...a: unknown[]) => mockGetPeaks(...a),
}));

const busy = (retryAfterMs: number | null = 5000) =>
  new ApiError('Waveform is being prepared', 503, { error: 'Waveform is being prepared' }, retryAfterMs, true);
const ok = { peaks: [0.1, 0.2], resolutionMs: 100 };

function setup(slug = 's', episode = 'e1', tick = 0) {
  const qc = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(
    ({ slug: sl, episode: ep, tick: t }) => usePeaks(sl, ep, 0, 100, t),
    { wrapper, initialProps: { slug, episode, tick } },
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  mockGetPeaks.mockReset();
});
afterEach(() => vi.useRealTimers());

describe('usePeaks 503 wait', () => {
  it('stays loading on a 503 and resolves once the file is ready', async () => {
    mockGetPeaks.mockRejectedValueOnce(busy()).mockResolvedValueOnce(ok);
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.peaks).toBeNull();
    expect(result.current.peaksError).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(4999));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(result.current.peaks).toEqual([0.1, 0.2]);
    expect(result.current.peaksError).toBeNull();
  });

  it('keeps polling on repeated 503s at the Retry-After delay', async () => {
    mockGetPeaks.mockRejectedValue(busy(8000));
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(7999));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(8000));
    expect(mockGetPeaks).toHaveBeenCalledTimes(3);
    expect(result.current.peaksError).toBeNull();
  });

  it('falls back to 5s without Retry-After and caps at 30s', async () => {
    mockGetPeaks.mockRejectedValueOnce(busy(null)).mockRejectedValueOnce(busy(999000)).mockResolvedValue(ok);
    setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(29999));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(mockGetPeaks).toHaveBeenCalledTimes(3);
  });

  it('waits the 1s floor when Retry-After is 0', async () => {
    mockGetPeaks.mockRejectedValueOnce(busy(0)).mockResolvedValue(ok);
    setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(999));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
  });

  it('waits the 5s default when Retry-After was sent but could not be read', async () => {
    mockGetPeaks.mockRejectedValueOnce(busy(null)).mockResolvedValue(ok);
    setup();
    await act(() => vi.advanceTimersByTimeAsync(4999));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
  });

  it('caps a huge Retry-After at 30s', async () => {
    mockGetPeaks.mockRejectedValueOnce(busy(86_400_000)).mockResolvedValue(ok);
    setup();
    await act(() => vi.advanceTimersByTimeAsync(29_999));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['400', new ApiError('nope', 400, {})],
    ['404', new ApiError('missing', 404, {})],
    ['500', new ApiError('broke', 500, {})],
    ['502 after the client retries', new ApiError('bad gateway', 502, {})],
    ['503 without Retry-After', new ApiError('unavailable', 503, {})],
  ])('surfaces a %s at once and schedules nothing', async (_name, error) => {
    mockGetPeaks.mockRejectedValue(error);
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.peaksError).toBe(error.message);
    expect(vi.getTimerCount()).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
  });

  it('passes an abort signal that fires on unmount', async () => {
    mockGetPeaks.mockRejectedValue(busy());
    const { unmount } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    const signal = mockGetPeaks.mock.calls[0][5] as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it('restarts the 10 minute bound when the tick changes during a wait', async () => {
    mockGetPeaks.mockRejectedValue(busy());
    const { result, rerender } = setup();
    await act(() => vi.advanceTimersByTimeAsync(9 * 60 * 1000));
    expect(result.current.peaksError).toBeNull();
    rerender({ slug: 's', episode: 'e1', tick: 1 });
    await act(() => vi.advanceTimersByTimeAsync(9 * 60 * 1000));
    expect(result.current.peaksError).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(61 * 1000));
    expect(result.current.peaksError).toBe('Waveform is being prepared');
  });

  it('cancels the timer on unmount', async () => {
    mockGetPeaks.mockRejectedValue(busy());
    const { unmount } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(vi.getTimerCount()).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(60000));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
  });

  it('aborts and stops polling the old episode when the episode changes', async () => {
    mockGetPeaks.mockRejectedValue(busy());
    const { rerender } = setup('s', 'e1');
    await act(() => vi.advanceTimersByTimeAsync(0));
    const oldSignal = mockGetPeaks.mock.calls[0][5] as AbortSignal;
    mockGetPeaks.mockClear();
    mockGetPeaks.mockResolvedValue(ok);
    rerender({ slug: 's', episode: 'e2', tick: 0 });
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(oldSignal.aborted).toBe(true);
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
    expect(mockGetPeaks.mock.calls[0][1]).toBe('e2');
  });

  it('shows the error after 10 minutes of 503s', async () => {
    mockGetPeaks.mockRejectedValue(busy());
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(9 * 60 * 1000));
    expect(result.current.peaksError).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(61 * 1000));
    expect(result.current.peaksError).toBe('Waveform is being prepared');
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('usePeaks retry after an error', () => {
  it('clears the error and refetches when the tick changes', async () => {
    mockGetPeaks.mockRejectedValueOnce(new ApiError('broke', 500, {})).mockResolvedValue(ok);
    const { result, rerender } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.peaksError).toBe('broke');
    rerender({ slug: 's', episode: 'e1', tick: 1 });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
    expect(result.current.peaksError).toBeNull();
    expect(result.current.peaks).toEqual([0.1, 0.2]);
  });

  it('retry() refetches and loading tracks the missing peaks', async () => {
    mockGetPeaks.mockRejectedValueOnce(new ApiError('broke', 500, {})).mockResolvedValue(ok);
    const { result } = setup();
    expect(result.current.loading).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.loading).toBe(false);
    act(() => result.current.retry());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(mockGetPeaks).toHaveBeenCalledTimes(2);
    expect(result.current.peaks).toEqual([0.1, 0.2]);
    expect(result.current.loading).toBe(false);
  });
});
