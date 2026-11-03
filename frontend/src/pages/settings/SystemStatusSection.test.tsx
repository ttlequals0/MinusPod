import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import DatabaseStatsSection from './DatabaseStatsSection';
import SystemStatusSection from './SystemStatusSection';
import type { SystemStatus } from '../../api/types';

const mockCheckpoint = vi.fn();
const mockChangelog = vi.fn();

vi.mock('../../api/settings', () => ({
  checkpointDatabase: (...args: unknown[]) => mockCheckpoint(...args),
  getChangelog: (...args: unknown[]) => mockChangelog(...args),
}));

vi.mock('./UpdateStatusPanel', () => ({ default: () => null }));
vi.mock('../../components/SystemHealthPanel', () => ({ default: () => null }));

const STATUS = {
  status: 'ok', version: '2.76.0', uptime: 120,
  feeds: { total: 1 },
  episodes: { total: 2, byStatus: {} },
  storage: { usedMb: 3, fileCount: 4 },
  settings: { retentionDays: 30, whisperModel: 'small', whisperDevice: 'cpu', baseUrl: '' },
  stats: { totalTimeSaved: 0, totalInputTokens: 0, totalOutputTokens: 0, totalLlmCost: 0 },
  database: {
    journalMode: 'wal', synchronous: 1, busyTimeoutMs: 5000,
    pageSizeBytes: 4096, pageCount: 10, freelistPages: 2,
    walAutocheckpointPages: 1000, databaseBytes: 1048576, walBytes: 524288, shmBytes: 32768,
    instrumentation: {
      scope: 'worker-process', processId: 42, slowStatements: 3, slowCommits: 1,
      failedCommits: 0, longTransactions: 2, lastCommitMs: 4.5, maxCommitMs: 12.25,
    },
  },
} satisfies SystemStatus;

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DatabaseStatsSection database={STATUS.database} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockCheckpoint.mockResolvedValue({
    busy: false, logPages: 8, checkpointedPages: 8, durationMs: 1.25,
  });
});

describe('DatabaseStatsSection', () => {
  it('shows worker-scoped diagnostics', async () => {
    renderSection();
    expect(screen.getByText(/worker process 42/)).toBeTruthy();
    expect(screen.getByText('12.25 ms')).toBeTruthy();
  });

  it('reports the passive checkpoint result', async () => {
    renderSection();
    await userEvent.click(screen.getByRole('button', { name: 'Run passive checkpoint' }));
    expect((await screen.findByRole('status')).textContent).toContain(
      'Checkpointed 8 of 8 WAL pages in 1.25 ms.',
    );
  });
});

function renderStatus(onToggle = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SystemStatusSection
          status={STATUS}
          statusLoading={false}
          whatsNewOnUpgrade
          onWhatsNewOnUpgradeChange={onToggle}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SystemStatusSection', () => {
  it('shows the instance cells', () => {
    renderStatus();
    for (const label of ['Version', 'Feeds', 'Episodes', 'Storage', 'Uptime']) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    for (const label of ['Time Saved', 'LLM Tokens', 'Cost']) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });

  it('opens the What\'s new dialog from the version button', async () => {
    mockChangelog.mockResolvedValue({
      current: '2.76.0',
      sections: [{ version: '2.76.0', date: '2026-01-01', body: '### Added\n- Something' }],
    });
    renderStatus();
    await userEvent.click(screen.getByRole('button', { name: '2.76.0' }));
    expect(await screen.findByRole('dialog', { name: "What's new in 2.76.0" })).toBeTruthy();
    expect(mockChangelog).toHaveBeenCalledWith({ limit: 10 });
    expect(await screen.findByText('Something')).toBeTruthy();
  });

  it('binds the upgrade toggle to the setting', async () => {
    const onToggle = vi.fn();
    renderStatus(onToggle);
    const toggle = screen.getByRole('switch', { name: "Show what's new after an upgrade" });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    await userEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
