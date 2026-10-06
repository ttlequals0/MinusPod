import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PatternCleanupSection from './PatternCleanupSection';
import { ApiError } from '../../api/client';
import type { PatternCleanupStatus } from '../../api/patternCleanup';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
const mockRun = vi.fn();
const mockCatalog = vi.fn();

vi.mock('../../api/patternCleanup', () => ({
  patternCleanupQueryKey: ['patternCleanup'],
  getPatternCleanupStatus: (...args: unknown[]) => mockGet(...args),
  updatePatternCleanupSettings: (...args: unknown[]) => mockUpdate(...args),
  runPatternCleanup: (...args: unknown[]) => mockRun(...args),
}));

vi.mock('../../hooks/useModelCatalog', () => ({
  useModelCatalog: (...args: unknown[]) => mockCatalog(...args),
}));

function makeStatus(overrides: Partial<PatternCleanupStatus> = {}): PatternCleanupStatus {
  return {
    enabled: true,
    cron: '0 4 * * 0',
    batchSize: 25,
    unusedDays: 90,
    provider: '',
    model: '',
    inProgress: false,
    lastRun: '2026-10-04T04:00:00Z',
    lastError: null,
    lastSummary: {
      runId: 3, status: 'completed', trigger: 'schedule', forced: false,
      reviewed: 12, suggested: 4, skipped: 2, errors: 0,
      model: 'm', provider: 'anthropic', credentialSlot: 'primary',
      startedAt: '2026-10-04T04:00:00Z', finishedAt: '2026-10-04T04:02:00Z',
      durationMs: 120000, error: null,
    },
    pending: { total: 4, byKind: { trim: 3, retire: 1 } },
    ...overrides,
  };
}

function renderSection(props: Partial<React.ComponentProps<typeof PatternCleanupSection>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PatternCleanupSection
        primaryProvider="anthropic"
        secondaryProvider="openrouter"
        secondaryEnabled
        detectionSlot="secondary"
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem('settings-section-pattern-cleanup', 'true');
  vi.clearAllMocks();
  mockGet.mockResolvedValue(makeStatus());
  mockUpdate.mockImplementation(async (body) => ({ ...makeStatus(), ...body }));
  mockRun.mockResolvedValue({ runId: 9 });
  mockCatalog.mockReturnValue({
    models: [{ id: 'big-model', name: 'Big Model' }, { id: 'small-model', name: 'Small Model' }],
    isLoading: false,
    isError: false,
  });
});

describe('PatternCleanupSection', () => {
  it('renders the settings from the status query', async () => {
    renderSection();
    const cron = (await screen.findByLabelText(/schedule \(cron\)/i)) as HTMLInputElement;
    expect(cron.value).toBe('0 4 * * 0');
    expect((screen.getByLabelText(/patterns per run/i) as HTMLInputElement).value).toBe('25');
    expect((screen.getByLabelText(/retire after/i) as HTMLInputElement).value).toBe('90');
    expect((screen.getByLabelText(/cleanup provider/i) as HTMLSelectElement).value).toBe('same_as_detection');
    expect((screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value).toBe('');
    expect(screen.getByText('A higher quality model gives better trims and splits')).toBeDefined();
    expect(screen.getByText('Experimental')).toBeDefined();
  });

  it('hides the schedule while disabled', async () => {
    mockGet.mockResolvedValue(makeStatus({ enabled: false }));
    renderSection();
    await screen.findByLabelText(/patterns per run/i);
    expect(screen.queryByLabelText(/schedule \(cron\)/i)).toBeNull();
  });

  it('save sends numbers and the slot string', async () => {
    const user = userEvent.setup();
    renderSection();
    const batch = await screen.findByLabelText(/patterns per run/i);
    await user.clear(batch);
    await user.type(batch, '50');
    const days = screen.getByLabelText(/retire after/i);
    await user.clear(days);
    await user.type(days, '120');
    await user.selectOptions(screen.getByLabelText(/cleanup provider/i), 'primary');
    await user.selectOptions(screen.getByLabelText(/cleanup model/i), 'big-model');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith({
      enabled: true, cron: '0 4 * * 0', batchSize: 50, unusedDays: 120,
      provider: 'primary', model: 'big-model',
    });
    expect(await screen.findByText(/^saved$/i)).toBeDefined();
  });

  it('shows a save error inline', async () => {
    mockUpdate.mockRejectedValue(new Error('invalid cron expression: bad'));
    const user = userEvent.setup();
    renderSection();
    await screen.findByLabelText(/schedule \(cron\)/i);
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    expect(await screen.findByText('invalid cron expression: bad')).toBeDefined();
  });

  it('loads the catalog for the chosen slot', async () => {
    const user = userEvent.setup();
    renderSection();
    await screen.findByLabelText(/cleanup provider/i);
    // Same as detection follows detection's resolved slot.
    expect(mockCatalog).toHaveBeenLastCalledWith('openrouter', 'secondary', true);
    await user.selectOptions(screen.getByLabelText(/cleanup provider/i), 'primary');
    expect(mockCatalog).toHaveBeenLastCalledWith('anthropic', 'primary', true);
  });

  it('falls back to the primary catalog when the secondary slot is unusable', async () => {
    mockGet.mockResolvedValue(makeStatus({ provider: 'secondary' }));
    renderSection({ secondaryProvider: '', secondaryEnabled: false, detectionSlot: 'primary' });
    await screen.findByLabelText(/cleanup provider/i);
    expect(mockCatalog).toHaveBeenLastCalledWith('anthropic', 'primary', true);
    expect(screen.getByText(/is off, so this stage runs on/i)).toBeDefined();
  });

  it('run now starts a run and refetches the status', async () => {
    const user = userEvent.setup();
    renderSection();
    await screen.findByLabelText(/patterns per run/i);
    expect(mockGet).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: /run now/i }));
    expect(mockRun).toHaveBeenCalledWith(false);
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it('shows Running... and disables both run buttons while a run is in progress', async () => {
    mockGet.mockResolvedValue(makeStatus({ inProgress: true }));
    renderSection();
    const running = await screen.findByRole('button', { name: /running/i });
    expect((running as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /force recheck all/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('explains a 409 from run now', async () => {
    mockRun.mockRejectedValue(new ApiError('cleanup_in_progress', 409));
    const user = userEvent.setup();
    renderSection();
    await screen.findByLabelText(/patterns per run/i);
    await user.click(screen.getByRole('button', { name: /run now/i }));
    expect(await screen.findByText('A cleanup run is already in progress.')).toBeDefined();
  });

  it('force recheck asks first, cancel does nothing, confirm runs with force', async () => {
    const user = userEvent.setup();
    renderSection();
    await screen.findByLabelText(/patterns per run/i);

    await user.click(screen.getByRole('button', { name: /force recheck all/i }));
    let dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/every learned pattern will be reviewed again/i)).toBeDefined();
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mockRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /force recheck all/i }));
    dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /^recheck all$/i }));
    expect(mockRun).toHaveBeenCalledWith(true);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('shows the last run counts and error', async () => {
    mockGet.mockResolvedValue(makeStatus({ lastError: 'provider unreachable' }));
    renderSection();
    expect(await screen.findByText(/12 reviewed, 4 suggested, 2 skipped/)).toBeDefined();
    expect(screen.getByText('provider unreachable')).toBeDefined();
    expect(screen.getByText(/4 suggestions/)).toBeDefined();
  });

  it('shows never when there is no last run', async () => {
    mockGet.mockResolvedValue(makeStatus({ lastRun: null, lastSummary: null }));
    renderSection();
    expect(await screen.findByText(/never/i)).toBeDefined();
  });

  it('renders Retry instead of the form when the GET fails', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderSection();
    expect(await screen.findByText(/could not load pattern cleanup settings/i)).toBeDefined();
    expect(screen.queryByRole('button', { name: /^save$/i })).toBeNull();
    mockGet.mockResolvedValue(makeStatus());
    await user.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByLabelText(/patterns per run/i)).toBeDefined();
  });
});
