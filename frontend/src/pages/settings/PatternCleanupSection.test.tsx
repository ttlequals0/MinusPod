import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import PatternCleanupSection from './PatternCleanupSection';
import { ApiError } from '../../api/client';
import type { PatternCleanupSettings, PatternCleanupStatus } from '../../api/patternCleanup';

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
    modelMissing: false,
    inProgress: false,
    lastRun: '2026-10-04T04:00:00Z',
    lastError: null,
    lastSummary: {
      id: 3, status: 'completed', trigger: 'schedule', forced: false,
      reviewedCount: 12, suggestedCount: 4, skippedCount: 2, errorCount: 0,
      model: 'm', provider: 'anthropic', credentialSlot: 'primary',
      startedAt: '2026-10-04T04:00:00Z', finishedAt: '2026-10-04T04:02:00Z', error: null,
    },
    pending: { total: 4, byKind: { trim: 3, retire: 1 } },
    ...overrides,
  };
}

function renderSection(props: Partial<React.ComponentProps<typeof PatternCleanupSection>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <PatternCleanupSection
          primaryProvider="anthropic"
          secondaryProvider="openrouter"
          secondaryEnabled
          detectionSlot="secondary"
          {...props}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { ...view, queryClient: client };
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
    expect((screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value).toBe('same_as_detection');
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
    const view = renderSection();
    view.queryClient.setQueryData(['settings'], {});
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
    expect(view.queryClient.getQueryState(['settings'])?.isInvalidated).toBe(true);
  });

  it('requires choosing an inherited or explicit model when cleanup model is missing', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(makeStatus({ model: '', modelMissing: true }));
    mockUpdate.mockImplementation(async (body) => {
      const saved = makeStatus({ ...body, modelMissing: false });
      mockGet.mockResolvedValue(saved);
      return saved;
    });
    renderSection();
    const model = await screen.findByLabelText(/cleanup model/i);
    expect((model as HTMLSelectElement).value).toBe('');
    expect(screen.getByText('Pick a model before processing episodes.')).toBeDefined();
    expect((screen.getByRole('button', { name: /run now/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /^save$/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(model).getAllByRole('option', { name: 'Same as detection model' })).toHaveLength(1);

    await user.selectOptions(model, 'same_as_detection');
    expect((screen.getByRole('button', { name: /run now/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /^save$/i }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(expect.objectContaining({ model: '' })));
    await waitFor(() => expect((screen.getByRole('button', { name: /run now/i }) as HTMLButtonElement).disabled)
      .toBe(false));
  });

  it('clears the cleanup model when its selected provider slot changes identity', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(makeStatus({ provider: 'primary', model: 'big-model' }));
    renderSection();
    await screen.findByLabelText(/cleanup model/i);

    await user.selectOptions(screen.getByLabelText(/cleanup provider/i), 'secondary');

    expect((screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value).toBe('');
  });

  it('keeps intentional model inheritance when the cleanup provider identity changes', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(makeStatus({ provider: '', model: '', modelMissing: false }));
    renderSection();
    const model = await screen.findByLabelText(/cleanup model/i);
    expect((model as HTMLSelectElement).value).toBe('same_as_detection');

    await user.selectOptions(screen.getByLabelText(/cleanup provider/i), 'primary');

    expect((screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value).toBe('same_as_detection');
    expect(screen.queryByText('Pick a model before processing episodes.')).toBeNull();
  });

  it('clears cleanup model after the bound provider account changes', async () => {
    mockGet.mockResolvedValue(makeStatus({ provider: 'primary', model: 'big-model' }));
    const view = renderSection();
    const model = await screen.findByLabelText(/cleanup model/i);
    expect((model as HTMLSelectElement).value).toBe('big-model');

    view.rerender(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <PatternCleanupSection
            primaryProvider="ollama"
            primaryBaseUrl="http://localhost:11434"
            secondaryProvider="openrouter"
            secondaryBaseUrl=""
            secondaryEnabled
            detectionSlot="secondary"
            providerIdentityHasUnsavedChanges
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect((screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value).toBe(''));
  });

  it('preserves inherited cleanup model after the bound account changes', async () => {
    mockGet.mockResolvedValue(makeStatus({ provider: '', model: '', modelMissing: false }));
    const view = renderSection();
    await screen.findByLabelText(/cleanup model/i);

    view.rerender(
      <MemoryRouter>
        <QueryClientProvider client={view.queryClient}>
          <PatternCleanupSection
            primaryProvider="anthropic"
            secondaryProvider="new-provider"
            secondaryEnabled
            detectionSlot="secondary"
            providerIdentityHasUnsavedChanges
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect((screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value).toBe('same_as_detection');
    expect(screen.queryByText('Pick a model before processing episodes.')).toBeNull();
  });

  it('keeps the imported cleanup model when the account and status are rehydrated', async () => {
    const view = renderSection();
    await screen.findByLabelText(/cleanup model/i);

    view.rerender(
      <MemoryRouter>
        <QueryClientProvider client={view.queryClient}>
          <PatternCleanupSection
            primaryProvider="openai-compatible"
            primaryBaseUrl="https://new.example/v1"
            secondaryProvider="openrouter"
            secondaryBaseUrl=""
            secondaryEnabled
            detectionSlot="secondary"
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    mockCatalog.mockReturnValue({
      models: [
        { id: 'big-model', name: 'Big Model' },
        { id: 'small-model', name: 'Small Model' },
        { id: 'imported-model', name: 'Imported Model' },
      ],
      isLoading: false,
      isError: false,
    });
    mockGet.mockResolvedValue(makeStatus({ provider: 'primary', model: 'imported-model' }));
    await view.queryClient.invalidateQueries({ queryKey: ['patternCleanup'] });

    await waitFor(() => expect(
      (screen.getByLabelText(/cleanup model/i) as HTMLSelectElement).value,
    ).toBe('imported-model'));
    expect(screen.queryByText('Save changes before starting a run.')).toBeNull();
    expect((screen.getByRole('button', { name: /run now/i }) as HTMLButtonElement).disabled).toBe(false);
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

  it('requires saving changed settings before starting either run', async () => {
    const user = userEvent.setup();
    mockUpdate.mockImplementation(async (body: Partial<PatternCleanupSettings>) => {
      const saved = makeStatus(body);
      mockGet.mockResolvedValue(saved);
      return saved;
    });
    renderSection();
    await screen.findByLabelText(/cleanup model/i);
    await user.selectOptions(screen.getByLabelText(/cleanup model/i), 'small-model');
    const runNow = screen.getByRole('button', { name: /run now/i }) as HTMLButtonElement;
    const force = screen.getByRole('button', { name: /force recheck all/i }) as HTMLButtonElement;
    expect(runNow.disabled).toBe(true);
    expect(force.disabled).toBe(true);
    expect(screen.getByText('Save changes before starting a run.')).toBeDefined();
    await user.click(runNow);
    expect(mockRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith(expect.objectContaining({ model: 'small-model' })));
    await waitFor(() => expect((screen.getByRole('button', { name: /run now/i }) as HTMLButtonElement).disabled)
      .toBe(false));
    await user.click(screen.getByRole('button', { name: /run now/i }));
    expect(mockRun).toHaveBeenCalledWith(false);
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
    expect(within(dialog).getByText(/every active learned pattern will be reviewed again/i)).toBeDefined();
    expect(within(dialog).getByText(/later manual or scheduled runs continue through the remaining patterns/i)).toBeDefined();
    expect(within(dialog).getByText(/force recheck all again restarts this process/i)).toBeDefined();
    expect(within(dialog).getByText(/each run sends up to 25 patterns to the llm/i)).toBeDefined();
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
    const link = screen.getByRole('link', { name: /4 suggestions/ });
    expect(link.getAttribute('href')).toBe('/patterns?tab=cleanup');
  });

  it('dates the last run by when it finished and shows a run in progress', async () => {
    mockGet.mockResolvedValue(makeStatus({ inProgress: true, lastRun: '2026-10-05T04:00:00Z' }));
    renderSection();
    expect(await screen.findByText('Running since:')).toBeDefined();
    const running = screen.getByText('Running since:').parentElement!;
    expect(running.textContent).toContain(new Date('2026-10-05T04:00:00Z').toLocaleString());
    const last = screen.getByText('Last run:').parentElement!;
    expect(last.textContent).toContain(new Date('2026-10-04T04:02:00Z').toLocaleString());
  });

  it('hides the running line when idle', async () => {
    renderSection();
    await screen.findByText('Last run:');
    expect(screen.queryByText('Running since:')).toBeNull();
  });

  it('does not fetch while the card is collapsed', async () => {
    localStorage.setItem('settings-section-pattern-cleanup', 'false');
    renderSection();
    await new Promise((r) => setTimeout(r, 20));
    expect(mockGet).not.toHaveBeenCalled();
    expect(mockCatalog).toHaveBeenLastCalledWith(expect.anything(), expect.anything(), false);
  });

  it('shows never when there is no last run', async () => {
    mockGet.mockResolvedValue(makeStatus({ lastRun: null, lastSummary: null }));
    renderSection();
    expect(await screen.findByText(/never/i)).toBeDefined();
  });

  it('scopes 44px control sizing to phones, not desktop', async () => {
    const { container } = renderSection();
    await screen.findByLabelText(/schedule \(cron\)/i);
    const tokens = (el: Element) => el.className.split(' ');
    const divs = Array.from(container.querySelectorAll('div'));

    const cronWrapper = divs.find((d) => tokens(d).includes('max-sm:[&_input]:min-h-11'));
    expect(tokens(cronWrapper!)).toContain('max-sm:[&_select]:min-h-11');
    expect(tokens(cronWrapper!)).toContain('max-sm:[&_button]:min-h-11');
    expect(tokens(cronWrapper!)).not.toContain('[&_input]:min-h-11');

    const providerWrapper = divs.find((d) => tokens(d).includes('max-sm:[&_label]:min-h-11'));
    expect(tokens(providerWrapper!)).toContain('max-sm:[&_select]:min-h-11');
    expect(tokens(providerWrapper!)).toContain('max-sm:[&_input]:min-h-11');
    expect(tokens(providerWrapper!)).toContain('max-sm:[&_button]:min-h-11');
    expect(tokens(providerWrapper!)).not.toContain('[&_label]:min-h-11');

    const batch = screen.getByLabelText(/patterns per run/i);
    expect(tokens(batch)).toContain('max-sm:min-h-11');
    expect(tokens(batch)).not.toContain('min-h-11');

    const run = screen.getByRole('button', { name: /run now/i });
    expect(tokens(run)).toContain('max-sm:min-h-11');
    expect(tokens(run)).not.toContain('min-h-11');
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
