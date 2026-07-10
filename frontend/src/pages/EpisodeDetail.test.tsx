/**
 * Component tests for the "Held for Review" section in EpisodeDetail.tsx (Phase C T6).
 *
 * Covers:
 *   - Held section renders N rows from pendingReviewMarkers; absent when empty.
 *   - Approve & Recut: confirm correction submitted then recut triggered (assert order).
 *   - hasOriginalAudio=false: recut NOT called, confirm still submitted, note shown.
 *   - Dismiss: reject correction submitted, no recut.
 *   - EpisodeList chip renders when pendingReviewCount>0, absent at 0/undefined.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EpisodeDetail from './EpisodeDetail';
import EpisodeList from '../components/EpisodeList';
import type { Episode, EpisodeDetail as EpisodeDetailType } from '../api/types';

// react-router-dom stubs
vi.mock('react-router-dom', () => ({
  useParams: () => ({ slug: 'test-feed', episodeId: 'ep-1' }),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

// Stub heavy child components that are not under test.
vi.mock('../components/AdEditor', () => ({
  default: () => <div data-testid="ad-editor" />,
}));
vi.mock('../components/PatternLink', () => ({
  default: ({ reason }: { reason: string }) => <span>{reason}</span>,
}));
vi.mock('../components/CollapsibleSection', () => ({
  default: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div>
      <div>{title}</div>
      {children}
    </div>
  ),
}));
vi.mock('../components/CueDetectionsSection', () => ({
  default: () => <div data-testid="cue-detections" />,
}));
vi.mock('../components/CueCandidatesSection', () => ({
  default: () => <div data-testid="cue-candidates" />,
}));
vi.mock('../components/PrevNextLink', () => ({
  default: () => null,
}));
vi.mock('../components/LoadingSpinner', () => ({
  default: () => <div data-testid="spinner" />,
}));
vi.mock('../components/Artwork', () => ({
  default: ({ alt }: { alt: string }) => <img alt={alt} />,
}));

vi.mock('../hooks/useLocalStorageState', () => ({
  useLocalStorageState: (_key: string, initial: unknown) => [initial, vi.fn()],
}));

vi.mock('../utils/confidence', () => ({
  formatConfidence: () => '95%',
}));

// Mutable mutation stubs; reassigned per test.
const mockSubmitCorrection = vi.fn();
const mockReprocessEpisode = vi.fn();

vi.mock('../api/feeds', () => ({
  getEpisode: vi.fn(),
  getFeed: vi.fn(),
  getOriginalTranscript: vi.fn(),
  reprocessEpisode: (...args: unknown[]) => mockReprocessEpisode(...args),
  regenerateChapters: vi.fn(),
}));

vi.mock('../api/patterns', () => ({
  submitCorrection: (...args: unknown[]) => mockSubmitCorrection(...args),
}));

// Minimal QueryClient wrapper; avoids pulling in the full provider.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

// Base episode fixture with pendingReviewMarkers.
const heldMarker = {
  start: 120,
  end: 360,
  confidence: 0.95,
  reason: 'sponsor match',
  held_for_review: true,
  hold_reason: 'max_duration' as const,
  validation: { decision: 'REVIEW' as const, adjusted_confidence: 0.95, flags: ['duration_cap'] },
};

function makeEpisode(overrides: Partial<EpisodeDetailType> = {}): EpisodeDetailType {
  return {
    id: 'ep-1',
    title: 'Test Episode',
    published: '2026-01-01T00:00:00Z',
    status: 'completed',
    hasOriginalAudio: true,
    corrections: [],
    pendingReviewMarkers: [heldMarker],
    ...overrides,
  } as EpisodeDetailType;
}

// Wrap getEpisode to return a resolved episode; getFeed returns minimal data.
import { getEpisode, getFeed } from '../api/feeds';

function setupEpisodeMock(ep: EpisodeDetailType) {
  (getEpisode as ReturnType<typeof vi.fn>).mockResolvedValue(ep);
  (getFeed as ReturnType<typeof vi.fn>).mockResolvedValue({ slug: 'test-feed', title: 'Feed', artworkUrl: null });
}

function renderDetail(ep: EpisodeDetailType) {
  const client = makeClient();
  setupEpisodeMock(ep);
  return render(
    <QueryClientProvider client={client}>
      <EpisodeDetail />
    </QueryClientProvider>,
  );
}

// ---- EpisodeDetail tests ----

describe('Held for Review section: rendering', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
  });

  it('renders a row for each pendingReviewMarker', async () => {
    const ep = makeEpisode({
      pendingReviewMarkers: [heldMarker, { ...heldMarker, start: 400, end: 500, hold_reason: 'no_cue_evidence' }],
    });
    renderDetail(ep);
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    // Two rows: two timespan pairs.
    expect(screen.getAllByText(/Held/).length).toBeGreaterThanOrEqual(2);
  });

  it('does not render the section when pendingReviewMarkers is empty', async () => {
    const ep = makeEpisode({ pendingReviewMarkers: [] });
    renderDetail(ep);
    await waitFor(() => {
      // Episode title appears = episode loaded.
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.queryByTestId('held-for-review-section')).toBeNull();
  });

  it('does not render the section when pendingReviewMarkers is absent', async () => {
    const ep = makeEpisode({ pendingReviewMarkers: undefined });
    renderDetail(ep);
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.queryByTestId('held-for-review-section')).toBeNull();
  });

  it('shows the hold_reason tooltip text for max_duration marker', async () => {
    renderDetail(makeEpisode());
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    const chip = screen.getByTitle("Exceeds the feed's max ad duration");
    expect(chip).toBeDefined();
  });

  it('shows the hold_reason tooltip text for no_cue_evidence marker', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'no_cue_evidence' }] }));
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    expect(screen.getByTitle('No audio-cue evidence')).toBeDefined();
  });
});

describe('Held for Review: Approve & Recut (hasOriginalAudio=true)', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
    mockSubmitCorrection.mockResolvedValue({});
    mockReprocessEpisode.mockResolvedValue({});
  });

  it('submits confirm correction then triggers recut, in that order', async () => {
    const user = userEvent.setup();
    const ep = makeEpisode({ hasOriginalAudio: true });
    renderDetail(ep);

    await waitFor(() => {
      expect(screen.getByTestId('approve-recut-0')).toBeDefined();
    });

    const callOrder: string[] = [];
    mockSubmitCorrection.mockImplementation(async () => { callOrder.push('correction'); return {}; });
    mockReprocessEpisode.mockImplementation(async () => { callOrder.push('recut'); return {}; });

    await user.click(screen.getByTestId('approve-recut-0'));

    await waitFor(() => {
      expect(callOrder).toContain('correction');
      expect(callOrder).toContain('recut');
    });
    // Correction must come before recut.
    expect(callOrder.indexOf('correction')).toBeLessThan(callOrder.indexOf('recut'));
  });

  it('calls submitCorrection with type=confirm for the correct marker', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: true }));

    await waitFor(() => {
      expect(screen.getByTestId('approve-recut-0')).toBeDefined();
    });

    await user.click(screen.getByTestId('approve-recut-0'));

    await waitFor(() => {
      expect(mockSubmitCorrection).toHaveBeenCalledTimes(1);
    });
    const [, , payload] = mockSubmitCorrection.mock.calls[0] as [string, string, { type: string }];
    expect(payload.type).toBe('confirm');
  });

  it('calls reprocessEpisode with mode=recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: true }));

    await waitFor(() => {
      expect(screen.getByTestId('approve-recut-0')).toBeDefined();
    });

    await user.click(screen.getByTestId('approve-recut-0'));

    await waitFor(() => {
      expect(mockReprocessEpisode).toHaveBeenCalledTimes(1);
    });
    expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut');
  });
});

describe('Held for Review: Approve without original audio (hasOriginalAudio=false)', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
    mockSubmitCorrection.mockResolvedValue({});
    mockReprocessEpisode.mockResolvedValue({});
  });

  it('submits confirm correction but does NOT call reprocessEpisode', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: false }));

    await waitFor(() => {
      expect(screen.getByTestId('approve-recut-0')).toBeDefined();
    });

    await user.click(screen.getByTestId('approve-recut-0'));

    await waitFor(() => {
      expect(mockSubmitCorrection).toHaveBeenCalledTimes(1);
    });
    expect(mockReprocessEpisode).not.toHaveBeenCalled();
  });

  it('shows "Saved - applies on next reprocess" note after approve without original', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: false }));

    await waitFor(() => {
      expect(screen.getByTestId('approve-recut-0')).toBeDefined();
    });

    await user.click(screen.getByTestId('approve-recut-0'));

    await waitFor(() => {
      expect(screen.queryByText(/applies on next reprocess/i)).not.toBeNull();
    });
  });
});

describe('Held for Review: Dismiss', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
    mockSubmitCorrection.mockResolvedValue({});
  });

  it('submits reject correction and does not call reprocessEpisode', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: true }));

    await waitFor(() => {
      expect(screen.getByTestId('dismiss-0')).toBeDefined();
    });

    await user.click(screen.getByTestId('dismiss-0'));

    await waitFor(() => {
      expect(mockSubmitCorrection).toHaveBeenCalledTimes(1);
    });
    const [, , payload] = mockSubmitCorrection.mock.calls[0] as [string, string, { type: string }];
    expect(payload.type).toBe('reject');
    expect(mockReprocessEpisode).not.toHaveBeenCalled();
  });
});

describe('Held for Review: failed Approve & Recut does not arm pendingRecutRef', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
  });

  it('does not call recut after correction fails, and a subsequent Dismiss does not trigger recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: true }));

    await waitFor(() => {
      expect(screen.getByTestId('approve-recut-0')).toBeDefined();
    });

    // First click: Approve & Recut - correction call fails.
    mockSubmitCorrection.mockRejectedValueOnce(new Error('network error'));
    await user.click(screen.getByTestId('approve-recut-0'));

    // Wait for error state to settle (saveStatus resets after error).
    await waitFor(() => {
      expect(mockSubmitCorrection).toHaveBeenCalledTimes(1);
    });
    // Recut must NOT have been called after the failed correction.
    expect(mockReprocessEpisode).not.toHaveBeenCalled();

    // Second action: Dismiss - correction succeeds, but recut must still not be called.
    mockSubmitCorrection.mockResolvedValue({});
    await user.click(screen.getByTestId('dismiss-0'));

    await waitFor(() => {
      expect(mockSubmitCorrection).toHaveBeenCalledTimes(2);
    });
    expect(mockReprocessEpisode).not.toHaveBeenCalled();
  });
});

// ---- EpisodeList chip tests ----

describe('EpisodeList: pending chip', () => {
  function makeEp(overrides: Partial<Episode> = {}): Episode {
    return {
      id: '1',
      title: 'Ep',
      published: '2026-01-01T00:00:00Z',
      status: 'completed',
      ...overrides,
    };
  }

  it('renders "N held" chip when pendingReviewCount > 0', () => {
    render(
      <EpisodeList
        episodes={[makeEp({ pendingReviewCount: 3 })]}
        feedSlug="test"
      />,
    );
    expect(screen.getByText('3 held')).toBeDefined();
  });

  it('does not render chip when pendingReviewCount is 0', () => {
    render(
      <EpisodeList
        episodes={[makeEp({ pendingReviewCount: 0 })]}
        feedSlug="test"
      />,
    );
    expect(screen.queryByText(/held/)).toBeNull();
  });

  it('does not render chip when pendingReviewCount is undefined', () => {
    render(
      <EpisodeList
        episodes={[makeEp()]}
        feedSlug="test"
      />,
    );
    expect(screen.queryByText(/held/)).toBeNull();
  });
});

describe('Differential status and corroboration badges', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the corroboration badge on a corroborated marker', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{
        start: 4160.9,
        end: 4477.0,
        confidence: 0.8,
        detection_stage: 'vad_gap',
        corroborated_by: 'transition_pair',
      }],
    }));
    await waitFor(() => expect(screen.getByText('Corroborated: transition')).toBeDefined());
  });

  it('omits the corroboration badge when corroborated_by is absent', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude' }],
    }));
    await waitFor(() => expect(screen.getByText('Detected Ads (1)')).toBeDefined());
    expect(screen.queryByText(/^Corroborated:/)).toBeNull();
  });

  it('labels dai_differential markers Cross-fetch', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 100, end: 160, confidence: 0.95, detection_stage: 'dai_differential' }],
    }));
    await waitFor(() => expect(screen.getByText('Cross-fetch')).toBeDefined());
  });

  it('shows the cross-fetch header badge with the inserted-region count', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      daiDifferential: {
        status: 'ok',
        regions: [
          { start_s: 0, end_s: 30.5, kind: 'differential', corr: 0.2 },
          { start_s: 30.5, end_s: 4100.0, kind: 'identical', corr: 0.99 },
          { start_s: 4100.0, end_s: 4142.4, kind: 'differential', corr: 0.1 },
        ],
      },
    }));
    await waitFor(() => expect(screen.getByText('Cross-fetch: 2 inserted')).toBeDefined());
  });

  it('shows no-diff and failed states', async () => {
    const { unmount } = renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      daiDifferential: { status: 'no_differential', regions: [] },
    }));
    await waitFor(() => expect(screen.getByText('Cross-fetch: no diff')).toBeDefined());
    unmount();
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      daiDifferential: { status: 'error', regions: [], error: 'refetch timed out' },
    }));
    await waitFor(() => expect(screen.getByText('Cross-fetch: failed')).toBeDefined());
  });

  it('omits the header badge when daiDifferential is absent', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.queryByText(/^Cross-fetch:/)).toBeNull();
  });
});

describe('New hold reasons: tooltip titles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the uncorroborated_tail title', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'uncorroborated_tail' }] }));
    await waitFor(() => expect(screen.getByTitle('Trailing ad with no audio evidence to back it')).toBeDefined());
  });

  it('shows the reviewer_contradiction title', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'reviewer_contradiction' }] }));
    await waitFor(() => expect(screen.getByTitle('The reviewer disagreed with the detected boundaries')).toBeDefined());
  });

  it('shows the no_splice_evidence title', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'no_splice_evidence' }] }));
    await waitFor(() => expect(screen.getByTitle('No splice artifact found at either edge')).toBeDefined());
  });
});

describe('Held for Review section: playback', () => {
  it('renders a play button per held row when original audio is retained', async () => {
    const ep = makeEpisode({
      pendingReviewMarkers: [heldMarker, { ...heldMarker, start: 400, end: 500 }],
    });
    renderDetail(ep);
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    expect(screen.getAllByLabelText('Play this ad')).toHaveLength(2);
  });

  it('hides the play button when the original audio is gone', async () => {
    renderDetail(makeEpisode({ hasOriginalAudio: false }));
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    expect(screen.queryByLabelText('Play this ad')).toBeNull();
  });
});
