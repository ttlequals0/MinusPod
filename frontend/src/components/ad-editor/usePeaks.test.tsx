import { renderHook, act, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePeaks } from './usePeaks';
import { WaveformError, WaveformLoading } from './WaveformStatus';

const ok = { peaks: [0.1, 0.2], resolutionMs: 100 };

function res(status: number, body: unknown, headers: Record<string, string> = {}) {
  const lower = Object.fromEntries(Object.entries({ 'content-type': 'application/json', ...headers }).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (n: string) => lower[n.toLowerCase()] ?? null },
    json: async () => body,
  } as Response;
}
const busy = (retryAfter: string | null = '5') =>
  res(503, { error: 'Waveform is being prepared' }, retryAfter === null ? {} : { 'Retry-After': retryAfter });

const fetchMock = vi.fn();

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
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('usePeaks 503 wait', () => {
  it('asks for the window at the editor resolution', async () => {
    fetchMock.mockResolvedValue(res(200, ok));
    setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/feeds\/s\/episodes\/e1\/peaks\?start=0&end=100&resolution_ms=100$/);
  });

  it('stays loading on a 503 and resolves once the file is ready', async () => {
    fetchMock.mockResolvedValueOnce(busy()).mockResolvedValueOnce(res(200, ok));
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.peaks).toBeNull();
    expect(result.current.peaksError).toBeNull();
    expect(result.current.loading).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(4999));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(result.current.peaks).toEqual([0.1, 0.2]);
    expect(result.current.peaksError).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('keeps polling on repeated 503s at the Retry-After delay', async () => {
    fetchMock.mockResolvedValue(busy('8'));
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(7999));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(8000));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result.current.peaksError).toBeNull();
  });

  it('falls back to 5s when the Retry-After value is unreadable and caps at 30s', async () => {
    fetchMock.mockResolvedValueOnce(busy('soon')).mockResolvedValueOnce(busy('999')).mockResolvedValue(res(200, ok));
    setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(4999));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(29_999));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('waits the 1s floor when Retry-After is 0', async () => {
    fetchMock.mockResolvedValueOnce(busy('0')).mockResolvedValue(res(200, ok));
    setup();
    await act(() => vi.advanceTimersByTimeAsync(999));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ['400', 400, 1],
    ['404', 404, 1],
    ['500', 500, 3],
    ['502', 502, 3],
    ['503 without Retry-After', 503, 3],
  ])('surfaces a %s after only the normal retries, never the long wait', async (_name, status, calls) => {
    fetchMock.mockResolvedValue(res(status, { error: 'nope' }));
    const { result } = setup();
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(result.current.peaksError).toBe('nope');
    expect(fetchMock).toHaveBeenCalledTimes(calls);
    expect(vi.getTimerCount()).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(fetchMock).toHaveBeenCalledTimes(calls);
  });

  it('passes an abort signal that fires on unmount', async () => {
    fetchMock.mockResolvedValue(busy());
    const { unmount } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it('restarts the 10 minute bound when the tick changes during a wait', async () => {
    fetchMock.mockResolvedValue(busy());
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
    fetchMock.mockResolvedValue(busy());
    const { unmount } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(vi.getTimerCount()).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts and stops polling the old episode when the episode changes', async () => {
    fetchMock.mockResolvedValue(busy());
    const { rerender } = setup('s', 'e1');
    await act(() => vi.advanceTimersByTimeAsync(0));
    const oldSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(res(200, ok));
    rerender({ slug: 's', episode: 'e2', tick: 0 });
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(oldSignal.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain('/episodes/e2/peaks');
  });

  it('shows the error after 10 minutes of 503s', async () => {
    fetchMock.mockResolvedValue(busy());
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
    fetchMock.mockResolvedValue(res(400, { error: 'broke' }));
    const { result, rerender } = setup();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.peaksError).toBe('broke');
    fetchMock.mockResolvedValue(res(200, ok));
    rerender({ slug: 's', episode: 'e1', tick: 1 });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.peaksError).toBeNull();
    expect(result.current.peaks).toEqual([0.1, 0.2]);
  });

  it('retry() refetches and loading tracks the missing peaks', async () => {
    fetchMock.mockResolvedValueOnce(res(400, { error: 'broke' })).mockResolvedValue(res(200, ok));
    const { result } = setup();
    expect(result.current.loading).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.loading).toBe(false);
    act(() => result.current.retry());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.peaks).toEqual([0.1, 0.2]);
    expect(result.current.loading).toBe(false);
  });
});

describe('editor waveform status while the file is prepared', () => {
  function Status() {
    const { loading, peaksError, retry } = usePeaks('s', 'e1', 0, 100, 0);
    if (peaksError) return <WaveformError onRetry={retry} />;
    return loading ? <WaveformLoading /> : <p>ready</p>;
  }
  const renderStatus = () => render(
    <QueryClientProvider client={new QueryClient()}><Status /></QueryClientProvider>,
  );

  it('shows Loading waveform... through the wait and the error with Retry after the bound', async () => {
    fetchMock.mockResolvedValue(busy());
    renderStatus();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText('Loading waveform...')).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(9 * 60 * 1000));
    expect(screen.getByText('Loading waveform...')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(61 * 1000));
    expect(screen.getByRole('alert').textContent).toBe('Could not load waveform. Try again.');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});
