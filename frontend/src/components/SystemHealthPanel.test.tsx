import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { SystemStatus } from '../api/types';
import SystemHealthPanel, { rollupHealth } from './SystemHealthPanel';

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

  it('is warning when some Podping nodes are failing', () => {
    expect(rollupHealth(status({
      podping: { listenerEnabled: true, allNodesDown: false, degradedSince: null, nodes: [
        { node: 'a', consecutiveFailures: 1, lastFailureReason: 'x', lastSuccessAt: null, nextRetryAt: null },
        { node: 'b', consecutiveFailures: 0, lastFailureReason: null, lastSuccessAt: null, nextRetryAt: null },
      ] },
    }))).toBe('warning');
  });
});

describe('SystemHealthPanel', () => {
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
    expect(screen.getByText(/1\/1 nodes healthy/)).toBeDefined();
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
});

describe('SystemHealthPanel: house recipes', () => {
  it('uses the shared badge shape and tint on the rollup pill', () => {
    render(<SystemHealthPanel status={status({})} />);
    const pill = screen.getByText('Healthy');
    expect(pill.className).toContain('px-2 py-0.5 text-xs rounded');
    expect(pill.className).toContain('bg-success/20');
  });

  it('gives the panel a card radius and the header a 44px tap target', () => {
    render(<SystemHealthPanel status={status({})} />);
    const trigger = screen.getByRole('button', { name: /system health/i });
    expect(trigger.className).toContain('min-h-[44px]');
    expect(trigger.parentElement?.className).toContain('rounded-lg');
  });
});
