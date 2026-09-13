/**
 * Tests for the queue-hold row in the global status bar.
 *
 * The bar hides itself when nothing is happening. A paused or waiting queue
 * is idle by that measure, so a hold has to count as activity or the one
 * state worth explaining is the one nobody sees.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import GlobalStatusBar from './GlobalStatusBar';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  resolve: (response: Response) => void;

  constructor(resolve: (response: Response) => void) {
    this.resolve = resolve;
    FakeEventSource.instances.push(this);
  }

  async emit(payload: unknown) {
    await act(async () => {
      this.resolve(new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    });
  }
}

function makeStatus(overrides = {}) {
  return {
    currentJob: null,
    queueLength: 0,
    queuedEpisodes: [],
    feedRefreshes: [],
    lastUpdated: Date.now() / 1000,
    ...overrides,
  };
}

function emptyHold(overrides = {}) {
  return {
    queuePaused: false,
    holdUntil: null,
    holdSince: null,
    offlineHeld: 0,
    offlineServices: [],
    ...overrides,
  };
}

/** The hold detail is one list item; the collapsed summary repeats the same
 *  words, so match on the row rather than on the text. */
function holdRow(match: string) {
  const row = screen.getAllByRole('listitem')
    .find((li) => li.textContent?.includes(match));
  expect(row).toBeDefined();
  return row as HTMLElement;
}

async function renderBar(status: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <GlobalStatusBar />
    </QueryClientProvider>,
  );
  await FakeEventSource.instances[0].emit(status);
  return utils;
}

function installStatusFetch() {
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
    new FakeEventSource(resolve);
  })));
}

describe('GlobalStatusBar queue holds', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    installStatusFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('stays hidden when the queue is idle and nothing is held', async () => {
    const { container } = await renderBar(makeStatus({ hold: emptyHold() }));
    expect(container.firstChild).toBeNull();
  });

  it('appears on an otherwise idle queue when a rate-limit pause is active', async () => {
    const holdUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    await renderBar(makeStatus({
      hold: emptyHold({ queuePaused: true, holdUntil }),
    }));
    // The chip says when the pause lifts, not that it started.
    expect(screen.getByText(/^Paused until \d{1,2}:\d{2}/)).toBeDefined();
  });

  it('hides once the pause has lifted', async () => {
    const { container } = await renderBar(makeStatus({ hold: emptyHold() }));
    expect(container.firstChild).toBeNull();
  });

  it('says when an active hold started alongside its reset time', async () => {
    const holdSince = new Date(Date.now() - 20 * 60 * 1000).toISOString();
    const holdUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    await renderBar(makeStatus({
      hold: emptyHold({ queuePaused: true, holdSince, holdUntil }),
    }));
    act(() => {
      screen.getByRole('button', { name: 'Expand status bar' }).click();
    });
    const detail = holdRow('Provider rate limit');
    // The reset time leads; when the pause began follows it.
    expect(detail.textContent).toMatch(/^Provider rate limit\. Resumes \d{1,2}:\d{2}/);
    expect(detail.textContent).toMatch(/Paused since \d{1,2}:\d{2}/);
  });

  it('names the unreachable service rather than only counting held episodes', async () => {
    await renderBar(makeStatus({
      hold: emptyHold({
        offlineHeld: 2,
        offlineServices: [{
          service: 'whisper', held: 2, reachable: false,
          checkedAt: new Date().toISOString(),
        }],
      }),
    }));
    expect(screen.getByText('Whisper endpoint unreachable')).toBeDefined();
  });

  it('shows the reset time once expanded', async () => {
    const holdUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    await renderBar(makeStatus({
      hold: emptyHold({ queuePaused: true, holdUntil }),
    }));
    act(() => {
      screen.getByRole('button', { name: 'Expand status bar' }).click();
    });
    const detail = holdRow('Provider rate limit');
    expect(detail.textContent).toContain('in 29m');
    expect(detail.textContent).toContain('Queued episodes wait in place');
  });

  it('says an offline wait does not stop the rest of the queue', async () => {
    await renderBar(makeStatus({
      hold: emptyHold({
        offlineHeld: 3,
        offlineServices: [{
          service: 'llm', held: 3, reachable: false,
          checkedAt: new Date().toISOString(),
        }],
      }),
    }));
    act(() => {
      screen.getByRole('button', { name: 'Expand status bar' }).click();
    });
    const detail = holdRow('LLM provider');
    expect(detail.textContent).toContain('3 episodes waiting');
    expect(detail.textContent).toContain('Others keep processing.');
  });

  it('reports a service as unchecked before the first probe', async () => {
    await renderBar(makeStatus({
      hold: emptyHold({
        offlineHeld: 1,
        offlineServices: [{
          service: 'llm', held: 1, reachable: null, checkedAt: null,
        }],
      }),
    }));
    act(() => {
      screen.getByRole('button', { name: 'Expand status bar' }).click();
    });
    expect(holdRow('not checked yet')).toBeDefined();
  });

  it('does not call a reachable service unchecked', async () => {
    // A service can hold episodes again between a recovery probe and the next
    // tick: reachable true with a real checkedAt must not read "not checked".
    await renderBar(makeStatus({
      hold: emptyHold({
        offlineHeld: 1,
        offlineServices: [{
          service: 'llm', held: 1, reachable: true,
          checkedAt: new Date().toISOString(),
        }],
      }),
    }));
    act(() => {
      screen.getByRole('button', { name: 'Expand status bar' }).click();
    });
    const detail = holdRow('LLM provider');
    expect(detail.textContent).toContain('reachable at last check');
    expect(detail.textContent).not.toContain('not checked yet');
  });

  it('tolerates a status frame with no hold block', async () => {
    const { container } = await renderBar(makeStatus());
    expect(container.firstChild).toBeNull();
  });
});

function job(slug: string, id: string, title: string, stage = 'transcribing', progress = 20) {
  return { slug, episodeId: id, title, podcastName: slug, stage, progress,
    startedAt: Date.now() / 1000 - 60, elapsed: 60 };
}

describe('GlobalStatusBar multiple jobs', () => {
  beforeEach(() => { FakeEventSource.instances = []; installStatusFetch(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('shows the oldest job collapsed with a chip for the rest', async () => {
    const a = job('feed-a', '1', 'First'); const b = job('feed-b', '2', 'Second');
    await renderBar(makeStatus({ currentJob: a, jobs: [a, b], hold: emptyHold() }));
    expect(screen.getByText('First')).toBeDefined();
    expect(screen.getByText('+1 running')).toBeDefined();
  });

  it('lists every job with its own stage once expanded', async () => {
    const a = job('feed-a', '1', 'First', 'transcribing');
    const b = job('feed-b', '2', 'Second', 'detecting', 55);
    await renderBar(makeStatus({ currentJob: a, jobs: [a, b], hold: emptyHold() }));
    act(() => { screen.getByRole('button', { name: 'Expand status bar' }).click(); });
    const rows = screen.getAllByTestId('status-job');
    expect(rows).toHaveLength(2);
    expect(rows[1].textContent).toContain('Second');
    expect(rows[1].textContent).toContain('Detecting ads');
  });

  it('expanded panel is capped to the viewport, not a fixed 192px', async () => {
    const a = job('feed-a', '1', 'First');
    const b = job('feed-b', '2', 'Second');
    await renderBar(makeStatus({ currentJob: a, jobs: [a, b], hold: emptyHold() }));
    act(() => { screen.getByRole('button', { name: 'Expand status bar' }).click(); });
    const panel = screen.getAllByTestId('status-job')[0].parentElement as HTMLElement;
    expect(panel.className).toContain('max-h-[min(70vh,26rem)]');
    expect(panel.className).not.toContain('max-h-48');
    expect(panel.className).toContain('overflow-y-auto');
  });
});

describe('GlobalStatusBar completion invalidation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    installStatusFetch();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('invalidates when one of several jobs finishes', async () => {
    const a = job('feed-a', '1', 'First');
    const b = job('feed-b', '2', 'Second');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    render(
      <QueryClientProvider client={client}>
        <GlobalStatusBar />
      </QueryClientProvider>,
    );
    const source = FakeEventSource.instances[0];
    await source.emit(makeStatus({ currentJob: a, jobs: [a, b], hold: emptyHold() }));
    invalidate.mockClear();
    // The newer job ends; currentJob still names the older one, so only the
    // key set says anything finished.
    await act(async () => { vi.advanceTimersByTime(2000); });
    await FakeEventSource.instances[1].emit(
      makeStatus({ currentJob: a, jobs: [a], hold: emptyHold() }));
    expect(invalidate.mock.calls.map((c) => c[0]?.queryKey)).toEqual([
      ['episode'], ['episodes'], ['feed'], ['feeds'],
    ]);
  });

  it('does not invalidate while the same jobs keep running', async () => {
    const a = job('feed-a', '1', 'First');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    render(
      <QueryClientProvider client={client}>
        <GlobalStatusBar />
      </QueryClientProvider>,
    );
    const source = FakeEventSource.instances[0];
    await source.emit(makeStatus({ currentJob: a, jobs: [a], hold: emptyHold() }));
    invalidate.mockClear();
    await act(async () => { vi.advanceTimersByTime(2000); });
    await FakeEventSource.instances[1].emit(
      makeStatus({ currentJob: a, jobs: [a], hold: emptyHold() }));
    expect(invalidate).not.toHaveBeenCalled();
  });
});
