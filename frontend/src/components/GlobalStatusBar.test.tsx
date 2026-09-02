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
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;

  constructor() {
    FakeEventSource.instances.push(this);
  }

  addEventListener() {}
  close() {}

  emit(payload: unknown) {
    act(() => {
      this.onopen?.();
      this.onmessage?.({ data: JSON.stringify(payload) });
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
    rateLimitHeld: 0,
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

function renderBar(status: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <GlobalStatusBar />
    </QueryClientProvider>,
  );
  FakeEventSource.instances[0].emit(status);
  return utils;
}

describe('GlobalStatusBar queue holds', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('stays hidden when the queue is idle and nothing is held', () => {
    const { container } = renderBar(makeStatus({ hold: emptyHold() }));
    expect(container.firstChild).toBeNull();
  });

  it('appears on an otherwise idle queue when a rate-limit pause is active', () => {
    const holdUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    renderBar(makeStatus({
      hold: emptyHold({ queuePaused: true, holdUntil, rateLimitHeld: 4 }),
    }));
    expect(screen.getByText('Queue paused')).toBeDefined();
  });

  it('names the unreachable service rather than only counting held episodes', () => {
    renderBar(makeStatus({
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

  it('shows the reset time and episode count once expanded', () => {
    const holdUntil = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    renderBar(makeStatus({
      hold: emptyHold({ queuePaused: true, holdUntil, rateLimitHeld: 1 }),
    }));
    act(() => {
      screen.getByRole('button', { name: 'Expand status bar' }).click();
    });
    const detail = holdRow('Provider rate limit');
    expect(detail.textContent).toContain('in 29m');
    // Singular, because one episode is waiting.
    expect(detail.textContent).toContain('1 episode waiting');
  });

  it('says an offline wait does not stop the rest of the queue', () => {
    renderBar(makeStatus({
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

  it('reports a service as unchecked before the first probe', () => {
    renderBar(makeStatus({
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

  it('does not call a reachable service unchecked', () => {
    // A service can hold episodes again between a recovery probe and the next
    // tick: reachable true with a real checkedAt must not read "not checked".
    renderBar(makeStatus({
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

  it('tolerates a status frame with no hold block', () => {
    const { container } = renderBar(makeStatus());
    expect(container.firstChild).toBeNull();
  });
});
