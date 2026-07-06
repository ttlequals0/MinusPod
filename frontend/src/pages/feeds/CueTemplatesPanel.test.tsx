/**
 * Component tests for the CueCrossEpisodeScanModal flow in CueTemplatesPanel.tsx
 * (Phase D, Task D1c).
 *
 * Covers:
 *   - "Find across episodes" button is disabled when <2 eligible episodes exist.
 *   - Picker filters out episodes where hasOriginalAudio === false.
 *   - Picker enforces 2-5 selection: Scan button disabled below 2, 5th episode
 *     cannot be added once max is reached.
 *   - First selected episode is marked "target".
 *   - Scan trigger fires crossEpisodeScan with the selected IDs.
 *   - Polling stops when status transitions from 'scanning' to 'ready'.
 *   - Candidates render with timespan and episodeMatches badge.
 *   - Make-template action opens CueMarkModal with correct bounds and target episode.
 *   - Zero candidates -> "No recurring segments found." message.
 *   - Error status -> error message shown.
 *
 * Also covers the "Optimize window" row action + inline result panel (Task D2b):
 *   - Trigger fires optimizeCueWindow; scanning spinner shows; poll stops on
 *     ready/error; error shows the saved message.
 *   - Result panel renders baseline/proposed times, scores, per-episode list.
 *   - Already-optimal state hides Apply; nullable baseline renders a dash.
 *   - Apply PATCHes sourceOffsetS/durationS and invalidates the templates query;
 *     Apply failure (409) shows an inline error and keeps the panel open.
 *   - Discard collapses without a PATCH; Rescan refires with rescan=true.
 *   - Row action disabled when the template has no source episode.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CueTemplatesPanel from './CueTemplatesPanel';
import type { CueTemplate } from '../../api/cueTemplates';
import type { Episode } from '../../api/types';

// ---- Router stub ----
vi.mock('react-router-dom', () => ({
  useParams: () => ({ slug: 'test-feed' }),
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

// ---- Heavy component stubs ----
vi.mock('../../components/CollapsibleSection', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('../../components/LoadingSpinner', () => ({
  default: ({ inline }: { inline?: boolean }) => (
    <span data-testid={inline ? 'spinner-inline' : 'spinner'} />
  ),
}));
vi.mock('../../components/CueMarkModal', () => ({
  default: ({
    episodeId,
    initialStart,
    initialEnd,
    onClose,
  }: {
    episodeId: string;
    initialStart?: number;
    initialEnd?: number;
    onClose: () => void;
  }) => (
    <div data-testid="cue-mark-modal">
      <span data-testid="modal-episode-id">{episodeId}</span>
      <span data-testid="modal-start">{initialStart}</span>
      <span data-testid="modal-end">{initialEnd}</span>
      <button onClick={onClose}>Close modal</button>
    </div>
  ),
}));

// ---- API mocks ----
const mockListCueTemplates = vi.fn();
const mockGetEpisodes = vi.fn();
const mockGetFeed = vi.fn();
const mockGetSettings = vi.fn();
const mockGetCueFeedAdvisory = vi.fn();
const mockCrossEpisodeScan = vi.fn();
const mockOptimizeCueWindow = vi.fn();
const mockUpdateCueTemplate = vi.fn();

vi.mock('../../api/cueTemplates', () => ({
  listCueTemplates: (...args: unknown[]) => mockListCueTemplates(...args),
  crossEpisodeScan: (...args: unknown[]) => mockCrossEpisodeScan(...args),
  optimizeCueWindow: (...args: unknown[]) => mockOptimizeCueWindow(...args),
  deleteCueTemplate: vi.fn(),
  importCueTemplate: vi.fn(),
  previewCueTemplate: vi.fn(),
  scanEpisodeCues: vi.fn(),
  suggestCueThreshold: vi.fn(),
  updateCueTemplate: (...args: unknown[]) => mockUpdateCueTemplate(...args),
  cueTemplateAudioUrl: (id: number) => `/audio/${id}`,
  cueTemplateExportUrl: (id: number) => `/export/${id}`,
  CUE_TYPE_OPTIONS: [
    { value: 'ad_break_boundary', label: 'Ad-break boundary (both ends)' },
  ],
}));

vi.mock('../../api/cueDetections', () => ({
  getCueFeedAdvisory: (...args: unknown[]) => mockGetCueFeedAdvisory(...args),
}));

vi.mock('../../api/feeds', () => ({
  getEpisode: vi.fn(),
  getEpisodes: (...args: unknown[]) => mockGetEpisodes(...args),
  getFeed: (...args: unknown[]) => mockGetFeed(...args),
  getFeeds: vi.fn().mockResolvedValue([]),
  updateFeed: vi.fn(),
  CUE_SCORE_MAX: 0.99,
}));

vi.mock('../../api/settings', () => ({
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
}));

// ---- Helpers ----
function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderPanel() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <CueTemplatesPanel slug="test-feed" />
    </QueryClientProvider>,
  );
}

function makeEpisode(id: string, title: string, hasOriginalAudio = true): Episode {
  return {
    id,
    title,
    published: '2026-01-01T00:00:00Z',
    status: 'completed',
    duration: 3600,
    hasOriginalAudio,
  };
}

// ---- Setup ----
beforeEach(() => {
  vi.clearAllMocks();

  mockListCueTemplates.mockResolvedValue([]);
  mockGetFeed.mockResolvedValue({ slug: 'test-feed', title: 'Test Feed' });
  mockGetSettings.mockResolvedValue({});
  mockGetCueFeedAdvisory.mockResolvedValue({ total: 0 });

  // Default picker: 3 eligible episodes
  mockGetEpisodes.mockResolvedValue({
    episodes: [
      makeEpisode('ep-1', 'Episode 1'),
      makeEpisode('ep-2', 'Episode 2'),
      makeEpisode('ep-3', 'Episode 3'),
    ],
    total: 3,
  });
});

// ---- Tests ----

describe('Find across episodes button', () => {
  it('is enabled when >= 2 eligible episodes exist', async () => {
    renderPanel();
    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /Find across episodes/i });
      expect(btn).toBeDefined();
      expect((btn as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it('is disabled when < 2 eligible episodes exist', async () => {
    mockGetEpisodes.mockResolvedValue({
      episodes: [makeEpisode('ep-1', 'Episode 1')],
      total: 1,
    });
    renderPanel();
    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /Find across episodes/i });
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it('is disabled when all episodes lack original audio', async () => {
    mockGetEpisodes.mockResolvedValue({
      episodes: [
        makeEpisode('ep-1', 'Episode 1', false),
        makeEpisode('ep-2', 'Episode 2', false),
      ],
      total: 2,
    });
    renderPanel();
    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /Find across episodes/i });
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    });
  });
});

describe('Episode picker', () => {
  it('filters out episodes where hasOriginalAudio is false', async () => {
    mockGetEpisodes.mockResolvedValue({
      episodes: [
        makeEpisode('ep-1', 'Good Episode'),
        makeEpisode('ep-2', 'No Audio', false),
        makeEpisode('ep-3', 'Another Good'),
      ],
      total: 3,
    });
    renderPanel();
    await waitFor(() => {
      expect((screen.getByRole('button', { name: /Find across episodes/i }) as HTMLButtonElement).disabled).toBe(false);
    });
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => {
      expect(screen.getByText('Good Episode')).toBeDefined();
      expect(screen.getByText('Another Good')).toBeDefined();
      // No-audio episode must not appear
      expect(screen.queryByText('No Audio')).toBeNull();
    });
  });

  it('Scan button is disabled with fewer than 2 episodes selected', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    const scanBtn = screen.getByRole('button', { name: /^Scan$/ });
    expect((scanBtn as HTMLButtonElement).disabled).toBe(true);

    // Select one episode -> still disabled
    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    expect((screen.getByRole('button', { name: /^Scan$/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Scan button is enabled when 2 episodes are selected', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));

    await waitFor(() => {
      expect((screen.getByRole('button', { name: /^Scan$/ }) as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it('enforces 5-episode max: 6th episode checkbox is disabled', async () => {
    mockGetEpisodes.mockResolvedValue({
      episodes: [
        makeEpisode('ep-1', 'Episode 1'),
        makeEpisode('ep-2', 'Episode 2'),
        makeEpisode('ep-3', 'Episode 3'),
        makeEpisode('ep-4', 'Episode 4'),
        makeEpisode('ep-5', 'Episode 5'),
        makeEpisode('ep-6', 'Episode 6'),
      ],
      total: 6,
    });
    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 6')).toBeDefined());

    // Select 5 episodes
    for (let i = 1; i <= 5; i++) {
      await userEvent.click(screen.getByLabelText(`Select episode Episode ${i}`));
    }

    // 6th should be disabled
    const ep6Checkbox = screen.getByLabelText('Select episode Episode 6') as HTMLInputElement;
    expect(ep6Checkbox.disabled).toBe(true);
  });

  it('marks the first selected episode as "target"', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));

    await waitFor(() => {
      expect(screen.getByText('target')).toBeDefined();
    });
  });
});

describe('Scan trigger and polling', () => {
  it('fires crossEpisodeScan with selected episode IDs', async () => {
    mockCrossEpisodeScan.mockResolvedValue({ status: 'scanning', candidates: [] });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => {
      expect(mockCrossEpisodeScan).toHaveBeenCalledWith('test-feed', ['ep-1', 'ep-2']);
    });
  });

  it('Rescan fires crossEpisodeScan with rescan=true', async () => {
    mockCrossEpisodeScan.mockResolvedValue({
      status: 'ready',
      targetEpisodeId: 'ep-1',
      episodeIds: ['ep-1', 'ep-2'],
      candidates: [],
    });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => expect(screen.getByText('No recurring segments found.')).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /^Rescan$/ }));

    await waitFor(() => {
      expect(mockCrossEpisodeScan).toHaveBeenCalledWith('test-feed', ['ep-1', 'ep-2'], true);
    });
  });

  it('shows scanning spinner while status is scanning', async () => {
    mockCrossEpisodeScan.mockResolvedValue({ status: 'scanning', candidates: [] });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => {
      expect(screen.getByTestId('spinner-inline')).toBeDefined();
    });
  });

  it('stops polling on status=ready and shows candidates', async () => {
    const candidate = { start: 30, end: 60, kind: 'recurring' as const, episodeMatches: 3 };
    // First response scanning, then ready: exercises one poll cycle end-to-end.
    mockCrossEpisodeScan
      .mockResolvedValueOnce({ status: 'scanning' })
      .mockResolvedValue({
        status: 'ready',
        targetEpisodeId: 'ep-1',
        episodeIds: ['ep-1', 'ep-2'],
        candidates: [candidate],
      });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    // Poll at 3s returns ready; badge appears.
    await waitFor(() => {
      expect(screen.getByText(/matched in 3 eps/i)).toBeDefined();
    }, { timeout: 5000 });
    expect(mockCrossEpisodeScan).toHaveBeenCalledTimes(2);

    // A further poll interval elapses with no new fetch: polling stopped.
    await new Promise((r) => setTimeout(r, 3300));
    expect(mockCrossEpisodeScan).toHaveBeenCalledTimes(2);
  }, 15000);

  it('shows error message when status is error', async () => {
    mockCrossEpisodeScan.mockResolvedValue({
      status: 'error',
      error: 'fingerprint decode failed',
      candidates: [],
    });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => {
      expect(screen.getByText('fingerprint decode failed')).toBeDefined();
    });
  });
});

describe('Candidate results', () => {
  it('shows "No recurring segments found." when candidates is empty', async () => {
    mockCrossEpisodeScan.mockResolvedValue({
      status: 'ready',
      targetEpisodeId: 'ep-1',
      episodeIds: ['ep-1', 'ep-2'],
      candidates: [],
    });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => {
      expect(screen.getByText('No recurring segments found.')).toBeDefined();
    });
  });

  it('Make template opens CueMarkModal with candidate bounds and target episode', async () => {
    const candidate = { start: 45.5, end: 90.2, kind: 'recurring' as const, episodeMatches: 2 };
    mockCrossEpisodeScan.mockResolvedValue({
      status: 'ready',
      targetEpisodeId: 'ep-1',
      episodeIds: ['ep-1', 'ep-2'],
      candidates: [candidate],
    });

    renderPanel();
    await waitFor(() => expect(screen.getByRole('button', { name: /Find across episodes/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Find across episodes/i }));
    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());

    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => {
      expect(screen.getByText(/matched in 2 ep/i)).toBeDefined();
    });

    // Click "Make template" on the candidate row
    await userEvent.click(screen.getByRole('button', { name: /Make template/i }));

    await waitFor(() => {
      expect(screen.getByTestId('cue-mark-modal')).toBeDefined();
    });
    expect(screen.getByTestId('modal-episode-id').textContent).toBe('ep-1');
    expect(screen.getByTestId('modal-start').textContent).toBe('45.5');
    expect(screen.getByTestId('modal-end').textContent).toBe('90.2');
  });
});

// ---- Optimize window (Task D2b) ----

function makeTemplate(overrides: Partial<CueTemplate> = {}): CueTemplate {
  return {
    id: 7,
    podcastId: 1,
    label: 'Ding',
    cueType: 'ad_break_boundary',
    sourceEpisodeId: 'ep-1',
    sourceOffsetS: 12.3,
    durationS: 2.5,
    sampleRate: 22050,
    nCoeffs: 13,
    scope: 'podcast',
    networkId: null,
    enabled: true,
    createdAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    hasAudio: false,
    ...overrides,
  };
}

function readyPayload(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ready',
    templateId: 7,
    proposedStartS: 11.9,
    proposedEndS: 15.1,
    meanPeakScore: 0.943,
    baselineMeanPeakScore: 0.871,
    perEpisode: [
      { episodeId: 'epaaaa1111', peakScore: 0.951 },
      { episodeId: 'epbbbb2222', peakScore: 0.938 },
    ],
    baselineWindow: { startS: 12.3, endS: 14.8 },
    ...overrides,
  };
}

async function openOptimizePanel() {
  await waitFor(() => expect(screen.getByText('Ding')).toBeDefined());
  await userEvent.click(screen.getByRole('button', { name: /Optimize window/i }));
}

describe('Optimize window row action', () => {
  beforeEach(() => {
    mockListCueTemplates.mockResolvedValue([makeTemplate()]);
  });

  it('fires optimizeCueWindow on click and shows the scanning state', async () => {
    mockOptimizeCueWindow.mockResolvedValue({ status: 'scanning', templateId: 7 });
    renderPanel();
    await openOptimizePanel();
    await waitFor(() => {
      expect(mockOptimizeCueWindow).toHaveBeenCalledWith('test-feed', 7);
      expect(screen.getByTestId('spinner-inline')).toBeDefined();
    });
  });

  it('is disabled when the template has no source episode', async () => {
    mockListCueTemplates.mockResolvedValue([makeTemplate({ sourceEpisodeId: null })]);
    renderPanel();
    await waitFor(() => expect(screen.getByText('Ding')).toBeDefined());
    const btn = screen.getByRole('button', { name: /Optimize window/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(mockOptimizeCueWindow).not.toHaveBeenCalled();
  });

  it('stops polling once ready and renders the comparison', async () => {
    mockOptimizeCueWindow
      .mockResolvedValueOnce({ status: 'scanning', templateId: 7 })
      .mockResolvedValue(readyPayload());
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => {
      expect(screen.getByText('Proposed')).toBeDefined();
    }, { timeout: 5000 });
    expect(mockOptimizeCueWindow).toHaveBeenCalledTimes(2);

    // A further poll interval elapses with no new fetch: polling stopped.
    await new Promise((r) => setTimeout(r, 3300));
    expect(mockOptimizeCueWindow).toHaveBeenCalledTimes(2);
  }, 15000);

  it('shows the saved error and stops polling on status=error', async () => {
    mockOptimizeCueWindow.mockResolvedValue({
      status: 'error',
      templateId: 7,
      error: 'decode failed on ep-2',
    });
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('decode failed on ep-2')).toBeDefined());
    const calls = mockOptimizeCueWindow.mock.calls.length;
    await new Promise((r) => setTimeout(r, 3300));
    expect(mockOptimizeCueWindow.mock.calls.length).toBe(calls);
  }, 15000);
});

describe('Optimize window result panel', () => {
  beforeEach(() => {
    mockListCueTemplates.mockResolvedValue([makeTemplate()]);
  });

  it('renders baseline vs proposed times, scores, and per-episode list', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload());
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('Proposed')).toBeDefined());
    expect(screen.getByText('Current')).toBeDefined();
    expect(screen.getByText('12.30s')).toBeDefined();
    expect(screen.getByText('14.80s')).toBeDefined();
    expect(screen.getByText('11.90s')).toBeDefined();
    expect(screen.getByText('15.10s')).toBeDefined();
    expect(screen.getByText('0.871')).toBeDefined();
    expect(screen.getByText('0.943')).toBeDefined();
    expect(screen.getByText(/epaaaa11 0\.951/)).toBeDefined();
    expect(screen.getByText(/epbbbb22 0\.938/)).toBeDefined();
  });

  it('shows already-optimal and hides Apply when proposed equals baseline', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload({
      proposedStartS: 12.3,
      proposedEndS: 14.8,
      meanPeakScore: 0.871,
    }));
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText(/Already optimal/i)).toBeDefined());
    expect(screen.queryByRole('button', { name: /^Apply$/ })).toBeNull();
  });

  it('renders a dash when the baseline score is null', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload({ baselineMeanPeakScore: null }));
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('Proposed')).toBeDefined());
    expect(screen.getByText('--')).toBeDefined();
  });

  it('Apply PATCHes sourceOffsetS/durationS and invalidates the templates query', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload());
    mockUpdateCueTemplate.mockResolvedValue(makeTemplate());
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('Proposed')).toBeDefined());
    const listCallsBefore = mockListCueTemplates.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: /^Apply$/ }));

    await waitFor(() => {
      expect(mockUpdateCueTemplate).toHaveBeenCalledWith(7, {
        sourceOffsetS: 11.9,
        durationS: 3.2,
      });
    });
    await waitFor(() => {
      expect(mockListCueTemplates.mock.calls.length).toBeGreaterThan(listCallsBefore);
    });
    // Panel closes after a successful apply.
    await waitFor(() => expect(screen.queryByText('Proposed')).toBeNull());
  });

  it('Apply failure shows an inline error and keeps the panel open', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload());
    mockUpdateCueTemplate.mockRejectedValue(
      new Error('Source episode original audio is no longer available'),
    );
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('Proposed')).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /^Apply$/ }));

    await waitFor(() => {
      expect(screen.getByText('Source episode original audio is no longer available')).toBeDefined();
    });
    expect(screen.getByText('Proposed')).toBeDefined();
  });

  it('Discard collapses the panel without a PATCH', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload());
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('Proposed')).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /^Discard$/ }));

    await waitFor(() => expect(screen.queryByText('Proposed')).toBeNull());
    expect(mockUpdateCueTemplate).not.toHaveBeenCalled();
  });

  it('Rescan refires with rescan=true', async () => {
    mockOptimizeCueWindow.mockResolvedValue(readyPayload());
    renderPanel();
    await openOptimizePanel();

    await waitFor(() => expect(screen.getByText('Proposed')).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /^Rescan$/ }));

    await waitFor(() => {
      expect(mockOptimizeCueWindow).toHaveBeenCalledWith('test-feed', 7, true);
    });
  });
});
