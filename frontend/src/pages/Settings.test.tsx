/**
 * Integration tests for per-prompt reset wiring on the Settings page (issue
 * #626): the two-click confirm fires the single-prompt reset endpoint (not
 * the bulk one), and a successful reset re-seeds the textarea from the
 * refetched settings the same way the existing "reset all prompts" button
 * does. Every section unrelated to prompts is stubbed; only PromptsSection
 * and ExperimentsSection render for real.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Settings from './Settings';
import type { Settings as SettingsShape, SettingValue } from '../api/types';

vi.mock('react-router', () => ({
  useLocation: () => ({ hash: '', pathname: '/settings', search: '' }),
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    isPasswordSet: true,
    logout: vi.fn(),
    refreshStatus: vi.fn(),
  }),
}));

// vi.mock calls are hoisted above imports/setup, so each stub must be a
// static top-level call rather than built from a loop over a name list.
vi.mock('./settings/SystemStatusSection', () => ({ default: () => null }));
vi.mock('./settings/StorageRetentionSection', () => ({ default: () => null }));
vi.mock('./settings/DataManagementSection', () => ({ default: () => null }));
vi.mock('./settings/NotificationsSection', () => ({ default: () => null }));
vi.mock('./settings/AuthenticatedFeedsSection', () => ({ default: () => null }));
vi.mock('./settings/SecuritySection', () => ({ default: () => null }));
vi.mock('./settings/ProcessingQueueSection', () => ({ default: () => null }));
vi.mock('./settings/AppearanceSection', () => ({ default: () => null }));
vi.mock('./settings/PodcastIndexSection', () => ({ default: () => null }));
vi.mock('./settings/LLMProviderSection', () => ({ default: () => null }));
vi.mock('./settings/AIModelsSection', () => ({ default: () => null }));
vi.mock('./settings/StageTunablesSection', () => ({ default: () => null }));
vi.mock('./settings/TranscriptionSection', () => ({ default: () => null }));
vi.mock('./settings/AudioSection', () => ({ default: () => null }));
vi.mock('./settings/CoverArtSection', () => ({ default: () => null }));
vi.mock('./settings/AdDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/GlobalDefaultsSection', () => ({ default: () => null }));
vi.mock('./settings/SegmentActionsSection', () => ({ default: () => null }));
vi.mock('./settings/Podcasting20Section', () => ({ default: () => null }));
vi.mock('./settings/AudioCueDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/PositionalPriorSection', () => ({ default: () => null }));
vi.mock('./settings/CommunityPatternsSection', () => ({ default: () => null }));
vi.mock('./settings/DatabaseBackupSection', () => ({ default: () => null }));
vi.mock('./settings/OfflineQueueSection', () => ({ default: () => null }));

const mockGetSettings = vi.fn();
const mockResetPrompt = vi.fn();

vi.mock('../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
  updateSettings: vi.fn(),
  resetSettings: vi.fn(),
  resetPrompts: vi.fn(),
  resetPrompt: (...a: unknown[]) => mockResetPrompt(...a),
  getModels: vi.fn().mockResolvedValue([]),
  getWhisperModels: vi.fn().mockResolvedValue([]),
  getSystemStatus: vi.fn().mockResolvedValue({}),
  runCleanup: vi.fn(),
  getProcessingEpisodes: vi.fn().mockResolvedValue([]),
  cancelProcessing: vi.fn(),
  refreshModels: vi.fn(),
  getRetention: vi.fn().mockResolvedValue({ retentionDays: 30, originalRetentionDays: 30, enabled: true }),
  updateRetention: vi.fn(),
  getProcessingTimeouts: vi.fn().mockResolvedValue({
    softTimeoutSeconds: 3600,
    hardTimeoutSeconds: 7200,
    defaults: { softTimeoutSeconds: 3600, hardTimeoutSeconds: 7200 },
    limits: { softMin: 60, hardMax: 86400 },
  }),
  updateProcessingTimeouts: vi.fn(),
  getAudioSettings: vi.fn().mockResolvedValue({ keepOriginalAudio: true }),
  updateAudioSettings: vi.fn(),
}));

vi.mock('../api/community', () => ({
  getReviewerSettings: vi.fn().mockResolvedValue({
    updatePatternsFromReviewerAdjustments: true, minTrimThreshold: 20, parallelAds: 4, parallelAdsDefault: 4,
  }),
  updateReviewerSettings: vi.fn(),
}));

vi.mock('../api/providers', () => ({
  listProviders: vi.fn().mockResolvedValue({}),
  updateProvider: vi.fn(),
  clearProvider: vi.fn(),
  testProvider: vi.fn(),
  testWhisperConnection: vi.fn(),
  testLlmConnection: vi.fn(),
  testPodcastIndex: vi.fn(),
}));

vi.mock('../api/feeds', () => ({
  refreshAllArtwork: vi.fn(),
}));

// Every field this test cares about is set explicitly; anything else falls
// back to a neutral SettingValue so the page's ~65-field hydration registry
// (which reads settings[key].value/.isDefault generically) never dereferences
// undefined for the many sections that are stubbed out above.
function sv(value: string, isDefault: boolean): SettingValue {
  return { value, isDefault };
}

function makeSettings(overrides: Partial<Record<string, unknown>> = {}): SettingsShape {
  const target: Record<string, unknown> = {
    systemPrompt: sv('custom system prompt', false),
    verificationPrompt: sv('default verification prompt', true),
    chapterPrompt: sv('default chapter prompt', true),
    reviewPrompt: sv('default review prompt', true),
    resurrectPrompt: sv('default resurrect prompt', true),
    systemPromptOverride: sv('', true),
    verificationPromptOverride: sv('', true),
    chapterPromptOverride: sv('', true),
    reviewPromptOverride: sv('', true),
    resurrectPromptOverride: sv('', true),
    ...overrides,
  };
  return new Proxy(target, {
    get(t, prop: string) {
      if (prop in t) return t[prop as keyof typeof t];
      return { value: '', isDefault: true };
    },
  }) as unknown as SettingsShape;
}

function makeClient() {
  return new QueryClient({
    // structuralSharing off: TanStack's default replaceEqualDeep would walk
    // the settings Proxy via Object.keys and rebuild it as a plain object,
    // silently dropping the fallback trap for every field the test doesn't
    // override.
    defaultOptions: { queries: { retry: false, staleTime: 0, structuralSharing: false }, mutations: { retry: false } },
  });
}

function renderSettings() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <Settings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Settings: per-prompt reset', () => {
  it('hides the system-prompt reset button once the field is back at default', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({ systemPrompt: sv('default system prompt', true) }));
    renderSettings();
    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toBeDefined();
    });
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
  });

  it('fires resetPrompt("system") on the second click, not resetPrompts', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockResetPrompt.mockResolvedValue({ value: 'default system prompt', isDefault: true });
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toBeDefined();
    });

    const [resetBtn] = screen.getAllByRole('button', { name: 'Reset' });
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));

    expect(mockResetPrompt).toHaveBeenCalledWith('system');
  });

  it('re-seeds the textarea with the default text once the reset lands', async () => {
    mockGetSettings.mockResolvedValueOnce(makeSettings());
    mockResetPrompt.mockResolvedValue({ value: 'default system prompt', isDefault: true });
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toHaveProperty('value', 'custom system prompt');
    });

    // The refetch after the mutation returns the field already at default.
    mockGetSettings.mockResolvedValue(makeSettings({ systemPrompt: sv('default system prompt', true) }));

    const [resetBtn] = screen.getAllByRole('button', { name: 'Reset' });
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toHaveProperty('value', 'default system prompt');
    });
  });

  it('fires resetPrompt("review") and resetPrompt("resurrect") from the Ad Reviewer section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      systemPrompt: sv('default system prompt', true),
      reviewPrompt: sv('custom review prompt', false),
      resurrectPrompt: sv('custom resurrect prompt', false),
    }));
    mockResetPrompt.mockResolvedValue({ value: 'default', isDefault: true });
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('Review prompt (confirm / adjust / reject)')).toBeDefined();
    });

    const resetButtons = screen.getAllByRole('button', { name: 'Reset' });
    expect(resetButtons).toHaveLength(2);

    await user.click(resetButtons[0]);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(mockResetPrompt).toHaveBeenCalledWith('review');

    await user.click(resetButtons[1]);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(mockResetPrompt).toHaveBeenCalledWith('resurrect');
  });
});
