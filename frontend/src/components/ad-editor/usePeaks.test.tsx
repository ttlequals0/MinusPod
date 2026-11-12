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
  new ApiError('Waveform is being prepared', 503, { error: 'Waveform is being prepared' }, retryAfterMs);
const ok = { peaks: [0.1, 0.2], resolutionMs: 100 };

function setup(slug = 's', episode = 'e1') {
  const qc = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(
    ({ slug: sl, episode: ep }) => usePeaks(sl, ep, 0, 100, 0),
    { wrapper, initialProps: { slug, episode } },
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

  it('surfaces a non-503 error at once without retrying', async () => {
    mockGetPeaks.mockRejectedValue(new ApiError('nope', 400, {}));
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.peaksError).toBe('nope');
    await act(() => vi.advanceTimersByTimeAsync(60000));
    expect(mockGetPeaks).toHaveBeenCalledTimes(1);
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

  it('stops polling the old episode when the episode changes', async () => {
    mockGetPeaks.mockRejectedValue(busy());
    const { rerender } = setup('s', 'e1');
    await act(() => vi.advanceTimersByTimeAsync(0));
    mockGetPeaks.mockClear();
    mockGetPeaks.mockResolvedValue(ok);
    rerender({ slug: 's', episode: 'e2' });
    await act(() => vi.advanceTimersByTimeAsync(60000));
    expect(mockGetPeaks.mock.calls.every((c) => c[1] === 'e2')).toBe(true);
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
