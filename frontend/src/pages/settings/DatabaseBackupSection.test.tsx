/**
 * Component tests for DatabaseBackupSection.tsx (Task B465-4).
 *
 * Covers:
 *   - Fields render from query data, including the formatted last-result size.
 *   - Save sends the full merged draft (template behavior).
 *   - A 400 from save shows the message inline.
 *   - Run-now success shows "Backup complete" and refetches (proves invalidation).
 *   - Run-now error shows the message, re-enables the button, and refetches.
 *   - Disabled state hides the cron input, keeps dest/keep-count, leaves run-now
 *     enabled, and shows lastRun as "never".
 *   - Malformed lastSummary does not crash and drops the "Last result:" row.
 *   - A failed GET renders an error + Retry (not the form); Retry refetches.
 *   - destWritable=false with an empty effectiveDest still shows the warning.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DatabaseBackupSection from './DatabaseBackupSection';
import type { DatabaseBackupSettings } from '../../api/settings';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
const mockRun = vi.fn();

vi.mock('../../api/settings', () => ({
  getDatabaseBackupSettings: (...args: unknown[]) => mockGet(...args),
  updateDatabaseBackupSettings: (...args: unknown[]) => mockUpdate(...args),
  runDatabaseBackupNow: (...args: unknown[]) => mockRun(...args),
}));

function makeSettings(overrides: Partial<DatabaseBackupSettings> = {}): DatabaseBackupSettings {
  return {
    enabled: true,
    cron: '30 3 * * *',
    dest: '/data/backups',
    effectiveDest: '/data/backups',
    destWritable: true,
    keepCount: 7,
    lastRun: '2026-07-06T09:00:00Z',
    lastError: null,
    lastSummary: JSON.stringify({
      path: '/data/backups/minuspod-20260706.db',
      sizeBytes: 5 * 1024 * 1024,
      durationMs: 1500,
      mode: 'rotate',
      keepCount: 7,
      prunedCount: 2,
      finishedAt: '2026-07-06T09:00:01Z',
    }),
    ...overrides,
  };
}

const runSummary = {
  path: '/data/backups/minuspod-20260706.db',
  sizeBytes: 5 * 1024 * 1024,
  durationMs: 1500,
  mode: 'rotate',
  keepCount: 7,
  prunedCount: 2,
  finishedAt: '2026-07-06T09:00:01Z',
};

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderSection() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <DatabaseBackupSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue(makeSettings());
  mockUpdate.mockResolvedValue(makeSettings());
  mockRun.mockResolvedValue(runSummary);
});

describe('DatabaseBackupSection', () => {
  it('renders fields from query data including the formatted last-result size', async () => {
    renderSection();

    const cron = (await screen.findByLabelText(/schedule \(cron\)/i)) as HTMLInputElement;
    expect(cron.value).toBe('30 3 * * *');
    expect((screen.getByLabelText(/destination/i) as HTMLInputElement).value).toBe('/data/backups');
    expect((screen.getByLabelText(/copies to keep/i) as HTMLInputElement).value).toBe('7');
    expect(screen.getByText(/5\.0 MB/)).toBeDefined();
    expect(screen.getByText(/1\.5s/)).toBeDefined();
  });

  it('save sends the full merged draft', async () => {
    const user = userEvent.setup();
    renderSection();

    const dest = await screen.findByLabelText(/destination/i);
    await user.clear(dest);
    await user.type(dest, '/mnt/nas');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith({
      enabled: true,
      cron: '30 3 * * *',
      dest: '/mnt/nas',
      keepCount: 7,
    });
    expect(await screen.findByText(/^saved$/i)).toBeDefined();
  });

  it('shows the message inline when save returns 400', async () => {
    mockUpdate.mockRejectedValue(new Error('invalid cron expression: bad'));
    const user = userEvent.setup();
    renderSection();

    await screen.findByLabelText(/schedule \(cron\)/i);
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText('invalid cron expression: bad')).toBeDefined();
  });

  it('run-now success shows "Backup complete" and refetches', async () => {
    const user = userEvent.setup();
    renderSection();

    await screen.findByLabelText(/schedule \(cron\)/i);
    expect(mockGet).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: /back up now/i }));

    expect(await screen.findByText(/backup complete/i)).toBeDefined();
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('run-now error shows the message, re-enables the button, and refetches', async () => {
    mockRun.mockRejectedValue(new Error('a backup is already in progress'));
    const user = userEvent.setup();
    renderSection();

    await screen.findByLabelText(/schedule \(cron\)/i);
    expect(mockGet).toHaveBeenCalledTimes(1);
    const button = screen.getByRole('button', { name: /back up now/i }) as HTMLButtonElement;
    await user.click(button);

    expect(await screen.findByText('a backup is already in progress')).toBeDefined();
    expect(button.disabled).toBe(false);
    // onSettled invalidates on error too, so lastError surfaces after a failed run.
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('disabled state hides cron, keeps dest and keep-count, leaves run-now enabled', async () => {
    mockGet.mockResolvedValue(makeSettings({ enabled: false, lastRun: null, lastSummary: null }));
    renderSection();

    await screen.findByLabelText(/destination/i);
    expect(screen.queryByLabelText(/schedule \(cron\)/i)).toBeNull();
    expect(screen.getByLabelText(/copies to keep/i)).toBeDefined();
    expect((screen.getByRole('button', { name: /back up now/i }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(/never/i)).toBeDefined();
  });

  it('does not crash on malformed lastSummary and drops the last-result row', async () => {
    mockGet.mockResolvedValue(makeSettings({ lastSummary: 'not json' }));
    renderSection();

    await screen.findByLabelText(/destination/i);
    expect(screen.queryByText(/last result:/i)).toBeNull();
  });

  it('renders an error with Retry (not the form) when the GET fails', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderSection();

    expect(await screen.findByText(/could not load backup settings/i)).toBeDefined();
    // The editable form must not render from fallback defaults.
    expect(screen.queryByLabelText(/destination/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /^save$/i })).toBeNull();

    // Retry refetches; make the next GET succeed and confirm the form appears.
    mockGet.mockResolvedValue(makeSettings());
    await user.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByLabelText(/destination/i)).toBeDefined();
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('shows the not-writable warning when effectiveDest came back empty', async () => {
    mockGet.mockResolvedValue(
      makeSettings({ destWritable: false, effectiveDest: '', dest: '/bad/dest' }),
    );
    renderSection();

    await screen.findByLabelText(/destination/i);
    expect(await screen.findByText(/is not[\s\S]*writable/i)).toBeDefined();
  });
});

describe('DatabaseBackupSection loading placeholder', () => {
  it('shows row skeletons instead of a Loading line while the query is pending', async () => {
    mockGet.mockReturnValueOnce(new Promise(() => {}));
    renderSection();
    expect(await screen.findByTestId('skeleton-rows')).toBeTruthy();
    expect(screen.queryByText('Loading...')).toBeNull();
  });

  it('drops the skeletons once the settings land', async () => {
    mockGet.mockResolvedValue(makeSettings());
    renderSection();
    await screen.findByLabelText(/destination/i);
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
  });
});
