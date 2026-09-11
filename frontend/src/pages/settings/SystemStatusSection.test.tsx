import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SystemStatusSection from './SystemStatusSection';
import type { SystemStatus } from '../../api/types';

const mockCheckpoint = vi.fn();
const mockGetAdmission = vi.fn();
const mockSetAdmission = vi.fn();

vi.mock('../../api/settings', () => ({
  checkpointDatabase: (...args: unknown[]) => mockCheckpoint(...args),
  getProcessingAdmission: (...args: unknown[]) => mockGetAdmission(...args),
  setProcessingAdmission: (...args: unknown[]) => mockSetAdmission(...args),
}));

vi.mock('./UpdateStatusPanel', () => ({ default: () => null }));

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
      <SystemStatusSection status={STATUS} statusLoading={false} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetAdmission.mockResolvedValue({ paused: false, activeRuns: 1, queuedEpisodes: 2 });
  mockSetAdmission.mockResolvedValue({ paused: true, activeRuns: 1, queuedEpisodes: 2 });
  mockCheckpoint.mockResolvedValue({
    busy: false, logPages: 8, checkpointedPages: 8, durationMs: 1.25,
  });
});

describe('SystemStatusSection database controls', () => {
  it('shows worker-scoped diagnostics and pauses new work', async () => {
    renderSection();
    expect(screen.getByText(/worker process 42/)).toBeTruthy();
    expect(screen.getByText('12.25 ms')).toBeTruthy();
    await userEvent.click(await screen.findByRole('button', { name: 'Pause new work' }));
    await waitFor(() => expect(mockSetAdmission).toHaveBeenCalledWith(true));
    expect(await screen.findByRole('button', { name: 'Resume new work' })).toBeTruthy();
  });

  it('reports the passive checkpoint result', async () => {
    renderSection();
    await userEvent.click(screen.getByRole('button', { name: 'Run passive checkpoint' }));
    expect((await screen.findByRole('status')).textContent).toContain(
      'Checkpointed 8 of 8 WAL pages in 1.25 ms.',
    );
  });
});
