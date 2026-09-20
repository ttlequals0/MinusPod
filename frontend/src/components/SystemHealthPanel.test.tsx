import type { ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SystemStatus } from '../api/types';
import SystemHealthPanel, { rollupHealth } from './SystemHealthPanel';

const requestPodpingCheck = vi.hoisted(() => vi.fn());

vi.mock('../api/settings', () => ({ requestPodpingCheck }));

function render(ui: ReactElement, queryClient = new QueryClient()) {
  return {
    ...rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>),
    queryClient,
  };
}

function status(over: Partial<SystemStatus>): SystemStatus {
  return {
    status: 'ok', version: '2.97.0', uptime: 1,
    feeds: { total: 0 }, episodes: { total: 0, byStatus: {} },
    storage: { usedMb: 0, fileCount: 0 },
    settings: { retentionDays: 0, whisperModel: 'small', whisperDevice: 'cpu', baseUrl: '' },
    stats: { totalTimeSaved: 0, totalInputTokens: 0, totalOutputTokens: 0, totalLlmCost: 0 },
    transcriber: { available: true, backend: 'local', device: 'cpu', lastOutcome: null },
    podping: { listenerEnabled: false, allNodesDown: false, degradedSince: null, nodes: [] },
    feedRefresh: { outageDegraded: false, outageAffectedCount: 0, lastSuccessfulRefreshAt: null, nextRetryAt: null },
    ...over,
  };
}

describe('rollupHealth', () => {
  it('is healthy when nothing is wrong', () => {
    expect(rollupHealth(status({}))).toBe('healthy');
  });

  it('is critical when the transcriber is unavailable', () => {
    expect(rollupHealth(status({
      transcriber: { available: false, backend: 'local', device: 'cuda', lastOutcome: null },
    }))).toBe('critical');
  });

  it('is critical when every enabled Podping node is down', () => {
    expect(rollupHealth(status({
      podping: { listenerEnabled: true, allNodesDown: true, degradedSince: '2026-09-14T00:00:00Z',
        nodes: [{ node: 'a', consecutiveFailures: 3, lastFailureReason: 'x', lastSuccessAt: null, nextRetryAt: null }] },
    }))).toBe('critical');
  });

  it('is warning when feed refresh is degraded', () => {
    expect(rollupHealth(status({
      feedRefresh: { outageDegraded: true, outageAffectedCount: 2, lastSuccessfulRefreshAt: null, nextRetryAt: null },
    }))).toBe('warning');
  });

  it('warns when observed Podping nodes have no active connection', () => {
    expect(rollupHealth(status({
      podping: { listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'a', consecutiveFailures: 1, lastFailureReason: 'x', lastSuccessAt: null, nextRetryAt: null },
        { node: 'b', consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: null, nextRetryAt: null },
      ] },
    }))).toBe('warning');
  });

  it('does not downgrade the rollup before any Podping node is observed', () => {
    expect(rollupHealth(status({
      podping: { listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'a', consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: null, nextRetryAt: null },
        { node: 'b', consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: null, nextRetryAt: null },
      ] },
    }))).toBe('healthy');
  });

  it('keeps an existing Podping outage critical while a node check is running', () => {
    expect(rollupHealth(status({
      podping: {
        listenerEnabled: true, allNodesDown: true,
        degradedSince: '2026-09-20T00:00:00Z', nodes: [],
        check: {
          checkId: 'check-1', status: 'running', requestedAt: '2026-09-20T00:00:00Z',
          startedAt: '2026-09-20T00:00:01Z', completedAt: null,
        },
      },
    }))).toBe('critical');
  });

  it('ignores historical Podping degradation while listening is disabled', () => {
    expect(rollupHealth(status({
      podping: {
        listenerEnabled: false, allNodesDown: true,
        degradedSince: '2026-09-20T00:00:00Z', nodes: [],
      },
    }))).toBe('healthy');
  });
});

describe('SystemHealthPanel', () => {
  beforeEach(() => {
    requestPodpingCheck.mockReset();
    requestPodpingCheck.mockResolvedValue({
      checkId: 'check-1', status: 'pending', requestedAt: '2026-09-20T00:00:00Z',
      startedAt: null, completedAt: null,
    });
  });

  it('renders nothing when no health fields are present', () => {
    const { container } = render(<SystemHealthPanel status={status({
      transcriber: undefined, podping: undefined, feedRefresh: undefined,
    })} />);
    expect(container.innerHTML).toBe('');
  });

  it('is collapsed by default and shows the overall label', () => {
    render(<SystemHealthPanel status={status({})} />);
    expect(screen.getByText('System health')).toBeDefined();
    expect(screen.getByText('Healthy')).toBeDefined();
    // Row detail is hidden until expanded.
    expect(screen.queryByText(/nodes healthy/)).toBeNull();
    expect(screen.getByRole('button', { name: /system health/i }).getAttribute('aria-expanded')).toBe('false');
  });

  it('expands to show each health row', () => {
    render(<SystemHealthPanel status={status({
      podping: { listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'a', consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: null, nextRetryAt: null },
      ] },
    })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    expect(screen.getByText('Transcriber')).toBeDefined();
    expect(screen.getByText(/0\/1 nodes healthy/)).toBeDefined();
    expect(screen.getByText('Feed refresh')).toBeDefined();
  });

  it('tints the transcriber row warning when the last transcription failed, matching the pill', () => {
    render(<SystemHealthPanel status={status({
      transcriber: {
        available: true, backend: 'local', device: 'cpu',
        lastOutcome: { status: 'failed', observedAt: '2026-09-14T00:00:00Z' },
      },
    })} />);
    expect(screen.getByText('Warning')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    const row = screen.getByText('Transcriber').parentElement?.parentElement;
    expect(row?.querySelector('span')?.className).toMatch(/bg-warning/);
  });

  it('shows a Critical pill when the transcriber is down', () => {
    render(<SystemHealthPanel status={status({
      transcriber: { available: false, backend: 'openai-api', device: null, lastOutcome: null },
    })} />);
    expect(screen.getByText('Critical')).toBeDefined();
  });

  it('shows only endpoint, status, and last seen in Podping details', () => {
    const lastSeen = new Date().toISOString();
    render(<SystemHealthPanel status={status({
      podping: { listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'api.one', selected: true, consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: lastSeen, nextRetryAt: null, httpStatus: 200, outcome: 'healthy' },
        { node: 'api.two', selected: false, consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: null, nextRetryAt: null },
      ] },
    })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    expect(screen.getByText('api.one')).toBeDefined();
    expect(screen.getByText('HTTP 200')).toBeDefined();
    expect(screen.getByText(/Last seen/)).toBeDefined();
    expect(screen.getByText('Not checked')).toBeDefined();
    expect(screen.queryByText('Last connected')).toBeNull();
    expect(screen.getByRole('button', { name: /podping details/i }).getAttribute('aria-controls')).toBe('podping-node-details');
  });

  it('hides raw Podping diagnostics and retry details', () => {
    const reason = 'HTTPSConnectionPool(host=example.invalid):MaxRetriesExceededWithoutBreaks';
    render(<SystemHealthPanel status={status({
      podping: { listenerEnabled: true, allNodesDown: true, degradedSince: null, nodes: [
        { node: 'example.invalid', consecutiveFailures: 1, lastFailureReason: reason, lastSuccessAt: null, nextRetryAt: null },
      ] },
    })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    expect(screen.getByText('Unreachable')).toBeDefined();
    expect(screen.queryByText(reason)).toBeNull();
    expect(screen.queryByText(/Retry/)).toBeNull();
  });

  it('does not call a malformed HTTP 200 response healthy', () => {
    render(<SystemHealthPanel status={status({
      podping: { listenerEnabled: true, allNodesDown: true, degradedSince: null, nodes: [
        { node: 'example.invalid', consecutiveFailures: 1, lastFailureReason: 'bad json', lastSuccessAt: null, nextRetryAt: null, httpStatus: 200, outcome: 'invalid_response' },
      ] },
    })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    expect(screen.getByText('Invalid response (HTTP 200)')).toBeDefined();
    expect(screen.queryByText('Healthy')).toBeNull();
  });

  it('keeps a healthy summary when the active node and a fallback are fresh', () => {
    const lastSeen = new Date().toISOString();
    const podping = {
      listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'active', active: true, consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: lastSeen, nextRetryAt: null, httpStatus: 200, outcome: 'healthy' as const },
        { node: 'fallback', active: false, consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: lastSeen, nextRetryAt: null, httpStatus: 200, outcome: 'healthy' as const },
        { node: 'down', active: false, consecutiveFailures: 2, lastFailureReason: 'offline', lastSuccessAt: null, nextRetryAt: null, outcome: 'unreachable' as const },
      ],
    };
    expect(rollupHealth(status({ podping }))).toBe('healthy');

    render(<SystemHealthPanel status={status({ podping })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    expect(screen.getByText('Active')).toBeDefined();
  });

  it('warns when the active node has no fresh fallback', () => {
    const lastSeen = new Date().toISOString();
    expect(rollupHealth(status({
      podping: { listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'active', active: true, consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: lastSeen, nextRetryAt: null, httpStatus: 200, outcome: 'healthy' },
      ] },
    }))).toBe('warning');
  });

  it('places the compact check action beside the Podping details trigger', () => {
    render(<SystemHealthPanel status={status({})} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    const details = screen.getByRole('button', { name: /podping details/i });
    const check = screen.getByRole('button', { name: 'Check now' });

    expect(check.parentElement).toBe(details.parentElement);
    expect(details.contains(check)).toBe(false);
    expect(check.className).toContain('min-h-11');
    expect(check.firstElementChild?.className).toContain('px-2 py-1');
    expect(check.firstElementChild?.className).toContain('text-xs');
  });

  it('offers a manual check while automatic listening is disabled', async () => {
    let finishRequest: ((value: Awaited<ReturnType<typeof requestPodpingCheck>>) => void) | undefined;
    requestPodpingCheck.mockImplementationOnce(() => new Promise((resolve) => {
      finishRequest = resolve;
    }));
    const current = status({});
    const queryClient = new QueryClient();
    queryClient.setQueryData(['status'], current);
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    render(<SystemHealthPanel status={current} />, queryClient);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));

    expect(requestPodpingCheck).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Checking...' }).hasAttribute('disabled')).toBe(true);
    finishRequest?.({
      checkId: 'check-1', status: 'pending', requestedAt: '2026-09-20T00:00:00Z',
      startedAt: null, completedAt: null,
    });
    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['status'] });
      expect((queryClient.getQueryData<SystemStatus>(['status']))?.podping?.check?.checkId).toBe('check-1');
    });
  });

  it('renders a newer authoritative check after its own request completes', async () => {
    const first = status({});
    const queryClient = new QueryClient();
    queryClient.setQueryData(['status'], first);
    const view = render(<SystemHealthPanel status={first} />, queryClient);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(requestPodpingCheck).toHaveBeenCalledOnce());

    const newer = status({
      podping: {
        listenerEnabled: false, allNodesDown: false, degradedSince: null, nodes: [],
        check: {
          checkId: 'check-2', status: 'error', requestedAt: '2026-09-20T00:01:00Z',
          startedAt: null, completedAt: null, message: 'Newer check failed.',
        },
      },
    });
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <SystemHealthPanel status={newer} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('alert').textContent).toBe('Newer check failed.');
  });

  it('summarizes a completed manual check while automatic listening is disabled', () => {
    render(<SystemHealthPanel status={status({
      podping: {
        listenerEnabled: false, allNodesDown: true,
        degradedSince: '2026-09-19T00:00:00Z', nodes: [],
        check: {
          checkId: 'check-1', status: 'completed', requestedAt: '2026-09-20T00:00:00Z',
          startedAt: '2026-09-20T00:00:01Z', completedAt: '2026-09-20T00:00:02Z',
          healthyNodes: 3, totalNodes: 4,
        },
      },
    })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    expect(screen.getByText(/3\/4 nodes healthy/)).toBeDefined();
    expect(screen.queryByText(/degraded since/i)).toBeNull();
    expect(screen.queryByText(/all 0 nodes down/i)).toBeNull();
  });

  it('shows a backend check error without claiming success', () => {
    render(<SystemHealthPanel status={status({
      podping: {
        listenerEnabled: false, allNodesDown: false, degradedSince: null, nodes: [],
        check: { checkId: 'check-1', status: 'error', requestedAt: '2026-09-20T00:00:00Z', startedAt: null, completedAt: null, message: 'Leader unavailable.' },
      },
    })} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    expect(screen.getByRole('alert').textContent).toBe('Leader unavailable.');
    expect(screen.queryByText(/check complete/i)).toBeNull();
  });

  it('shows the request error returned when a manual check cannot start', async () => {
    requestPodpingCheck.mockRejectedValue(new Error('Podping monitor leader is unavailable.'));
    render(<SystemHealthPanel status={status({})} />);
    fireEvent.click(screen.getByRole('button', { name: /system health/i }));
    fireEvent.click(screen.getByRole('button', { name: /podping details/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));

    expect((await screen.findByRole('alert')).textContent).toBe('Podping monitor leader is unavailable.');
  });
});

describe('SystemHealthPanel: house recipes', () => {
  it('uses the shared badge shape and tint on the rollup pill', () => {
    render(<SystemHealthPanel status={status({})} />);
    const pill = screen.getByText('Healthy');
    expect(pill.className).toContain('px-2 py-0.5 text-xs rounded');
    expect(pill.className).toContain('bg-success/20');
    expect(pill.className).toContain('text-success-on-tint');
  });

  it('gives the panel a card radius and the header a 44px tap target', () => {
    render(<SystemHealthPanel status={status({})} />);
    const trigger = screen.getByRole('button', { name: /system health/i });
    expect(trigger.className).toContain('min-h-[44px]');
    expect(trigger.parentElement?.className).toContain('rounded-lg');
  });
});
