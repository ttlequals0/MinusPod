/**
 * apiRequest's retry-on-429 behavior (round-2 review finding 2): a 429
 * must retry (bounded, honoring the schedule in RETRY_DELAYS) rather than
 * immediately reject, and when the response carries a numeric-seconds
 * Retry-After header that value is used for the wait instead of the fixed
 * backoff schedule.
 *
 * fetch is mocked directly (not through an api/*.ts wrapper) since these
 * tests exercise apiRequest itself, the shared retry machinery every
 * wrapper funnels through.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiError, apiFileRequest, apiRequest, sleep } from './client';

// Minimal fetch Response stand-in: only the surface apiRequest actually
// reads (status/ok/headers.get/json), so this doesn't depend on whatever
// Response polyfill (or lack of one) the test environment provides.
function fakeResponse(body: unknown, status: number, headers: Record<string, string> = {}) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  if (!lower['content-type']) lower['content-type'] = 'application/json';
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    json: async () => body,
  } as Response;
}

describe('apiRequest: retry on 429', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('does not replay a POST after a retryable response', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fakeResponse({ error: 'rate limited' }, 429, { 'Retry-After': '2' }))
      .mockResolvedValueOnce(fakeResponse({ staged: ['a.mp3'], rejected: [] }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/feeds/show/import/upload', {
      method: 'POST',
      body: new FormData(),
    }).catch((error: Error) => error);

    expect(await promise).toBeInstanceOf(Error);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('reports an uncertain result without replaying a POST after a network error', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);

    const error = await apiRequest('/settings/webhooks', {
      method: 'POST',
      body: { url: 'https://example.com/hook' },
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toContain('may have completed');
    expect((error as Error).message).toContain('Check the current state');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retries a POST only when the caller opts in', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fakeResponse({ error: 'rate limited' }, 429))
      .mockResolvedValueOnce(fakeResponse({ ok: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/idempotent-action', { method: 'POST', retry: true });
    await vi.advanceTimersByTimeAsync(1000);

    expect(await promise).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('falls back to the fixed backoff schedule when Retry-After is absent', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(fakeResponse({ error: 'rate limited' }, 429))
      .mockResolvedValueOnce(fakeResponse({ ok: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/x', { method: 'GET' });
    await vi.advanceTimersByTimeAsync(1000); // RETRY_DELAYS[0]
    const result = await promise;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result).toEqual({ ok: true });
  });

  it('gives up after 3 attempts (2 retries) and throws, rather than retrying forever', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ error: 'rate limited' }, 429));
    vi.stubGlobal('fetch', fetchMock);

    const promise = apiRequest('/x', { method: 'GET' });
    // Swallow the eventual rejection so it doesn't surface as an
    // unhandled rejection while timers are still being advanced below.
    const caught = promise.catch((e: Error) => e);

    await vi.advanceTimersByTimeAsync(1000); // RETRY_DELAYS[0]
    await vi.advanceTimersByTimeAsync(3000); // RETRY_DELAYS[1]

    const err = await caught;
    expect(err).toBeInstanceOf(Error);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe('ApiError: conflict bodies', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the jobState a 409 reported so callers can reconcile eligibility', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse(
      { error: 'Episode is currently processing', status: 409, jobState: 'processing' }, 409,
    )));

    const error = await apiRequest('/episodes/show/ep-1/reprocess', { method: 'POST' })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).jobState).toBe('processing');
    expect((error as ApiError).message).toBe('Episode is currently processing');
  });

  it('reports no jobState when the error body carries none', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse({ error: 'nope' }, 400)));

    const error = await apiRequest('/x', { method: 'POST' }).catch((e: unknown) => e);

    expect((error as ApiError).jobState).toBeUndefined();
  });
});

describe('413 mapping', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const htmlResponse = () => ({
    ok: false,
    status: 413,
    headers: { get: () => 'text/html' },
    json: async () => { throw new SyntaxError('Unexpected token <'); },
  } as unknown as Response);

  it('explains a proxy 413 with an HTML body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(htmlResponse()));

    const error = await apiRequest('/x', { method: 'POST' }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(413);
    expect((error as ApiError).message).toBe('The file is too large for the server or a proxy in front of it.');
  });

  it('keeps the server error text for a JSON 413', async () => {
    const msg = 'File is too large. This server accepts up to 1024 MB per upload.';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(fakeResponse({ error: msg, status: 413, maxUploadMb: 1024 }, 413)));

    const error = await apiRequest('/x', { method: 'POST' }).catch((e: unknown) => e);

    expect((error as ApiError).message).toBe(msg);
  });

  it('maps a proxy 413 in apiFileRequest too', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(htmlResponse()));

    const error = await apiFileRequest('/x', { method: 'POST' }).catch((e: unknown) => e);

    expect((error as Error).message).toBe('The file is too large for the server or a proxy in front of it.');
  });
});

describe('apiRequest: 503 with retry off', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('throws once and exposes Retry-After on the error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ error: 'busy' }, 503, { 'Retry-After': '7' }));
    vi.stubGlobal('fetch', fetchMock);

    const error = await apiRequest('/x', { retry: false }).catch((e: unknown) => e);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).retryAfterMs).toBe(7000);
  });
});

describe('apiRequest: 503 with Retry-After', () => {
  const busy = () => fakeResponse({ error: 'busy' }, 503, { 'Retry-After': '5' });
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('waits the Retry-After delay and retries until the request succeeds, within the budget', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(busy()).mockResolvedValueOnce(busy()).mockResolvedValueOnce(busy())
      .mockResolvedValue(fakeResponse({ ok: 1 }, 200));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 });
    await vi.advanceTimersByTimeAsync(4999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await promise).toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it.each([
    ['0', 1000],
    ['999', 30_000],
    ['soon', 5000],
    ['Wed, 21 Oct 2026 07:28:00 GMT', 5000],
  ])('waits Retry-After %j for %i ms inside the budget', async (header, ms) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(fakeResponse({ error: 'busy' }, 503, { 'Retry-After': header }))
      .mockResolvedValue(fakeResponse({ ok: 1 }, 200));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 });
    await vi.advanceTimersByTimeAsync(ms - 1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await promise;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps waiting without spending the generic retries, then throws the 503 once the budget is spent', async () => {
    const fetchMock = vi.fn().mockResolvedValue(busy());
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 62_000 }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(66_000);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(13);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).retryAfterMs).toBe(5000);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries like a 429 without a budget: the header sets the wait and the attempts are capped', async () => {
    const fetchMock = vi.fn().mockResolvedValue(busy());
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x').catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(4999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(5000);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((error as ApiError).status).toBe(503);
  });

  it.each([502, 504, 429, 500])('keeps the normal retry for a %i inside the budget', async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ error: 'x' }, status));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(5000);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((error as ApiError).status).toBe(status);
  });

  it('keeps the normal retry for a 503 without Retry-After inside the budget', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeResponse({ error: 'down' }, 503));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(3000);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((error as ApiError).retryAfterMs).toBeNull();
  });

  it.each(['POST', 'PUT', 'DELETE'])('never retries a %s on a 503, whatever the budget', async (method) => {
    const fetchMock = vi.fn().mockResolvedValue(busy());
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { method, busyBudgetMs: 600_000 }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(60_000);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((error as ApiError).status).toBe(503);
  });

  it('stops waiting when the caller aborts', async () => {
    const fetchMock = vi.fn().mockResolvedValue(busy());
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    const promise = apiRequest('/x', { busyBudgetMs: 600_000, signal: controller.signal }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(1000);
    expect(vi.getTimerCount()).toBe(1);
    controller.abort(new Error('unmounted'));
    expect(((await promise) as Error).message).toBe('unmounted');
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('resets the generic attempts after each busy answer, so spread-out network blips do not end the wait', async () => {
    const down = new TypeError('Failed to fetch');
    const fetchMock = vi.fn().mockRejectedValueOnce(down).mockResolvedValueOnce(busy()).mockRejectedValueOnce(down)
      .mockResolvedValueOnce(busy()).mockRejectedValueOnce(down).mockResolvedValue(fakeResponse({ ok: 1 }, 200));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await promise).toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  it('still fails after three network errors in a row with no busy answer between them', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await promise).toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('does not wait on a mutating request even with retry: true and a budget', async () => {
    const fetchMock = vi.fn().mockResolvedValue(busy());
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { method: 'POST', retry: true, busyBudgetMs: 600_000 }).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(60_000);
    const error = await promise;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((error as ApiError).status).toBe(503);
  });

  it('retries a network error', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue(fakeResponse({ ok: 1 }, 200));
    vi.stubGlobal('fetch', fetchMock);
    const promise = apiRequest('/x', { busyBudgetMs: 600_000 });
    await vi.advanceTimersByTimeAsync(2000);
    expect(await promise).toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('sleep', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('removes its abort listener when the timer fires', async () => {
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, 'addEventListener');
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    for (let i = 0; i < 5; i++) {
      const done = sleep(10, controller.signal);
      await vi.advanceTimersByTimeAsync(10);
      await done;
    }
    expect(add).toHaveBeenCalledTimes(5);
    expect(remove).toHaveBeenCalledTimes(5);
  });

  it('still rejects with the reason when aborted during the wait', async () => {
    const controller = new AbortController();
    const done = sleep(1000, controller.signal).catch((e: unknown) => e);
    controller.abort(new Error('stop'));
    expect(((await done) as Error).message).toBe('stop');
  });
});
