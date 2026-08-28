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

// react-router stubs
vi.mock('react-router', () => ({
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
vi.mock('../components/CollapsibleSection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components/CollapsibleSection')>();
  return {
    useCollapsibleOpen: actual.useCollapsibleOpen,
    default: ({ title, children }: { title: string; children: React.ReactNode }) => (
      <div>
        <div>{title}</div>
        {children}
      </div>
    ),
  };
});
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
  readStoredValue: (_key: string, fallback: unknown) => fallback,
}));

vi.mock('../utils/confidence', () => ({
  formatConfidence: () => '95%',
}));

// Mutable mutation stubs; reassigned per test.
const mockSubmitCorrection = vi.fn();
const mockReprocessEpisode = vi.fn();
const mockRegenerateChapters = vi.fn();
const mockUpdateLocalEpisode = vi.fn();
const mockUploadLocalEpisodeArtwork = vi.fn();

vi.mock('../api/feeds', () => ({
  getEpisode: vi.fn(),
  getFeed: vi.fn(),
  getOriginalTranscript: vi.fn(),
  reprocessEpisode: (...args: unknown[]) => mockReprocessEpisode(...args),
  regenerateChapters: (...args: unknown[]) => mockRegenerateChapters(...args),
  episodeOriginalUrl: (slug: string, episodeId: string) =>
    `/api/v1/feeds/${slug}/episodes/${episodeId}/original.mp3`,
  updateLocalEpisode: (...args: unknown[]) => mockUpdateLocalEpisode(...args),
  uploadLocalEpisodeArtwork: (...args: unknown[]) => mockUploadLocalEpisodeArtwork(...args),
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
    // Matches the default 'completed' status: a completed episode has, by
    // construction, finished at least one processing run. Tests exercising
    // "never processed" override this to null explicitly.
    processedAt: '2026-01-01T00:00:00Z',
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

  it('labels a verification_miss marker "Verification catch" instead of the generic Held chip', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'verification_miss' }] }));
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    const chip = screen.getByTitle('A standalone catch from the verification pass, held for a second opinion');
    expect(chip.textContent).toBe('Verification catch');
  });

  it('labels a differential_uncorroborated marker "Differential hold" instead of the generic Held chip', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'differential_uncorroborated' }] }));
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    const chip = screen.getByTitle('Audio differs across fetches with no corroborating signal');
    expect(chip.textContent).toBe('Differential hold');
  });

  it('labels a large VAD gap hold with its safety reason', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, hold_reason: 'large_vad_gap_extension' }] }));
    await waitFor(() => {
      expect(screen.getByTestId('held-for-review-section')).toBeDefined();
    });
    const chip = screen.getByTitle('Untranscribed audio exceeded the safe adjacency-only extension limit');
    expect(chip.textContent).toBe('VAD extension limit');
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

describe('Failure reason display', () => {
  it('shows the error panel for a permanently failed episode', async () => {
    renderDetail(makeEpisode({
      status: 'permanently_failed',
      error: 'Error code: 403 - Key limit exceeded (monthly limit)',
      pendingReviewMarkers: [],
    }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.getByText('Processing failed permanently')).toBeDefined();
    expect(
      screen.getByText('Error code: 403 - Key limit exceeded (monthly limit)'),
    ).toBeDefined();
  });

  it('shows the error panel for a failed episode', async () => {
    renderDetail(makeEpisode({
      status: 'failed',
      error: 'Transcription failed: corrupt audio',
      pendingReviewMarkers: [],
    }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.getByText('Processing failed')).toBeDefined();
    expect(screen.getByText('Transcription failed: corrupt audio')).toBeDefined();
  });

  it('does not show the panel for a completed episode', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [] }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.queryByText('Processing failed')).toBeNull();
    expect(screen.queryByText('Processing failed permanently')).toBeNull();
  });

  it('does not show the panel when a failed episode has no error text', async () => {
    renderDetail(makeEpisode({ status: 'permanently_failed', error: null, pendingReviewMarkers: [] }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.queryByText('Processing failed permanently')).toBeNull();
  });

  it('puts the error text on the status badge tooltip', async () => {
    renderDetail(makeEpisode({
      status: 'permanently_failed',
      error: 'Key limit exceeded',
      pendingReviewMarkers: [],
    }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.getByTitle('Key limit exceeded')).toBeDefined();
  });
});

// ---- Issue #509: batch approve-and-recut ----

const secondHeldMarker = {
  ...heldMarker,
  start: 500,
  end: 620,
  reason: 'another sponsor read',
};

const confirmedHeldCorrection = {
  id: 1,
  correction_type: 'confirm' as const,
  original_bounds: { start: heldMarker.start, end: heldMarker.end },
  created_at: '2026-01-02T00:00:00Z',
};

describe('Held for Review: batch approve (multiple held ads)', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
    mockSubmitCorrection.mockResolvedValue(undefined);
    mockReprocessEpisode.mockResolvedValue(undefined);
  });

  it('approving one of several held ads submits the correction without recutting', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({
      hasOriginalAudio: true,
      pendingReviewMarkers: [heldMarker, secondHeldMarker],
    }));
    await screen.findByTestId('held-for-review-section');

    await user.click(screen.getByTestId('approve-recut-0'));
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledTimes(1));
    expect(mockReprocessEpisode).not.toHaveBeenCalled();
  });

  it('multi-held approve button is labeled Approve, not Approve & Recut', async () => {
    renderDetail(makeEpisode({
      hasOriginalAudio: true,
      pendingReviewMarkers: [heldMarker, secondHeldMarker],
    }));
    await screen.findByTestId('held-for-review-section');
    expect(screen.getByTestId('approve-recut-0').textContent).toBe('Confirm ad');
  });

  it('apply bar shows the approved count and fires exactly one recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({
      hasOriginalAudio: true,
      pendingReviewMarkers: [{ ...heldMarker, approved: true }, secondHeldMarker],
      corrections: [confirmedHeldCorrection],
    }));
    await screen.findByTestId('held-for-review-section');

    const applyBtn = await screen.findByTestId('apply-approved-recut');
    expect(applyBtn.textContent).toBe('Apply 1 confirmed & recut');

    await user.click(applyBtn);
    await waitFor(() =>
      expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut'));
    expect(mockReprocessEpisode).toHaveBeenCalledTimes(1);
  });

  it('no apply bar without original audio', async () => {
    renderDetail(makeEpisode({
      hasOriginalAudio: false,
      pendingReviewMarkers: [{ ...heldMarker, approved: true }, secondHeldMarker],
      corrections: [confirmedHeldCorrection],
    }));
    await screen.findByTestId('held-for-review-section');
    expect(screen.queryByTestId('apply-approved-recut')).toBeNull();
  });

  it('approving the last unreviewed held ad recuts in one tap', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({
      hasOriginalAudio: true,
      pendingReviewMarkers: [{ ...heldMarker, approved: true }, secondHeldMarker],
      corrections: [confirmedHeldCorrection],
    }));
    await screen.findByTestId('held-for-review-section');

    // The approved card has no buttons; the remaining card offers the
    // one-tap finish since its approval completes the set.
    const btn = screen.getByTestId('approve-recut-1');
    expect(btn.textContent).toBe('Confirm & Recut');
    await user.click(btn);
    await waitFor(() =>
      expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut'));
  });

  it('single held ad keeps the one-tap approve and recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({
      hasOriginalAudio: true,
      pendingReviewMarkers: [heldMarker],
    }));
    await screen.findByTestId('held-for-review-section');

    await user.click(screen.getByTestId('approve-recut-0'));
    await waitFor(() =>
      expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut'));
  });
});


describe('Held for Review: apply bar guards', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
    mockSubmitCorrection.mockResolvedValue(undefined);
    mockReprocessEpisode.mockResolvedValue(undefined);
  });

  it('apply button is disabled while the episode is processing', async () => {
    renderDetail(makeEpisode({
      status: 'processing',
      hasOriginalAudio: true,
      pendingReviewMarkers: [{ ...heldMarker, approved: true }, secondHeldMarker],
      corrections: [confirmedHeldCorrection],
    }));
    await screen.findByTestId('held-for-review-section');
    const applyBtn = screen.getByTestId('apply-approved-recut');
    expect(applyBtn).toHaveProperty('disabled', true);
  });

  it('legacy confirmed hold without the approved flag still counts', async () => {
    // Confirms recorded before 2.51.0 carry no marker flag; the correction
    // join is the fallback so their apply action does not disappear.
    renderDetail(makeEpisode({
      hasOriginalAudio: true,
      pendingReviewMarkers: [heldMarker],
      corrections: [confirmedHeldCorrection],
    }));
    await screen.findByTestId('held-for-review-section');
    const applyBtn = await screen.findByTestId('apply-approved-recut');
    expect(applyBtn.textContent).toBe('Apply 1 confirmed & recut');
  });
});

describe('Detected ads: inline audition', () => {
  it('renders a play button on each detected ad row when original audio exists', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude' }],
      hasOriginalAudio: true,
    }));
    expect(await screen.findByText('Detected Ads (1)')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Play this ad' })).not.toBeNull();
    // The shared windowed player must mount for detected-only episodes
    // (no held or rejected rows), or the button would silently no-op.
    expect(document.querySelector('audio[preload="metadata"]')).not.toBeNull();
  });

  it('renders no play button when the original audio is gone', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude' }],
      hasOriginalAudio: false,
    }));
    expect(await screen.findByText('Detected Ads (1)')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Play this ad' })).toBeNull();
  });
});

describe('Segment category chips (#565)', () => {
  it('shows the category label on a detected ad marker', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude', category: 'cross_promo' }],
    }));
    expect(await screen.findByText('Detected Ads (1)')).not.toBeNull();
    expect(screen.getByText('Cross-promo')).not.toBeNull();
  });

  it('shows a muted Kept badge when actionApplied is keep', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude', category: 'sponsor', actionApplied: 'keep' }],
    }));
    expect(await screen.findByText('Detected Ads (1)')).not.toBeNull();
    expect(screen.getByText('Kept')).not.toBeNull();
  });

  it('omits the Kept badge when actionApplied is remove', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      adMarkers: [{ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude', category: 'sponsor', actionApplied: 'remove' }],
    }));
    expect(await screen.findByText('Detected Ads (1)')).not.toBeNull();
    expect(screen.queryByText('Kept')).toBeNull();
  });

  it('shows the category label on a Held for Review row', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [{ ...heldMarker, category: 'self_promo' }],
    }));
    expect(await screen.findByTestId('held-for-review-section')).not.toBeNull();
    expect(screen.getByText('Self-promo')).not.toBeNull();
  });

  it('shows the category label and Kept badge on a Detections Not Cut row', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      rejectedAdMarkers: [{
        start: 5, end: 20, confidence: 0.4, category: 'interaction', actionApplied: 'keep',
      }],
    }));
    expect(await screen.findByText('Detections Not Cut (1)')).not.toBeNull();
    expect(screen.getByText('Interaction')).not.toBeNull();
    expect(screen.getByText('Kept')).not.toBeNull();
  });
});

describe('Kept segments section (2.78.3)', () => {
  it('renders a row with the category and Kept badge when keptMarkers is populated', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      keptMarkers: [{ start: 127.8, end: 140.2, confidence: 0.9, category: 'intro', actionApplied: 'keep' }],
    }));
    expect(await screen.findByTestId('kept-segments-section')).not.toBeNull();
    expect(screen.getByText('Kept segments (1)')).not.toBeNull();
    expect(screen.getByText('Intro')).not.toBeNull();
    expect(screen.getByText('Kept')).not.toBeNull();
  });

  it('offers a play button per row when the original audio is retained', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      hasOriginalAudio: true,
      adMarkers: [],
      keptMarkers: [
        { start: 127.8, end: 140.2, confidence: 0.9, category: 'intro', actionApplied: 'keep' },
        { start: 300.0, end: 330.0, confidence: 0.8, category: 'self_promo', actionApplied: 'keep' },
      ],
    }));
    await screen.findByTestId('kept-segments-section');

    expect(screen.getAllByRole('button', { name: /play|audition/i }).length).toBeGreaterThanOrEqual(2);
  });

  it('omits the play button when the original audio is gone', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      hasOriginalAudio: false,
      adMarkers: [],
      keptMarkers: [{ start: 127.8, end: 140.2, confidence: 0.9, category: 'intro', actionApplied: 'keep' }],
    }));
    await screen.findByTestId('kept-segments-section');

    expect(screen.queryAllByRole('button', { name: /play|audition/i })).toHaveLength(0);
  });

  it('renders nothing when keptMarkers is empty', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [], keptMarkers: [] }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).not.toBeNull();
    });
    expect(screen.queryByTestId('kept-segments-section')).toBeNull();
    expect(screen.queryByText(/^Kept segments/)).toBeNull();
  });

  it('renders nothing when keptMarkers is absent', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [], keptMarkers: undefined }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).not.toBeNull();
    });
    expect(screen.queryByTestId('kept-segments-section')).toBeNull();
  });

  it('does not also render kept segments under Detections Not Cut', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      keptMarkers: [{ start: 1562.5, end: 1600.0, confidence: 0.9, category: 'self_promo', actionApplied: 'keep' }],
      rejectedAdMarkers: [],
    }));
    expect(await screen.findByTestId('kept-segments-section')).not.toBeNull();
    expect(screen.queryByText(/Detections Not Cut/)).toBeNull();
  });
});

describe('Correction submit error toast (#565)', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
  });

  it('surfaces the backend 409 message when a kept marker is corrected', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: true }));

    await waitFor(() => {
      expect(screen.getByTestId('dismiss-0')).toBeDefined();
    });

    mockSubmitCorrection.mockRejectedValueOnce(
      new Error('This segment is kept for this feed and cannot be corrected'),
    );
    await user.click(screen.getByTestId('dismiss-0'));

    expect(await screen.findByText(
      'This segment is kept for this feed and cannot be corrected',
    )).not.toBeNull();
  });

  it('dismissing the toast clears it', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ hasOriginalAudio: true }));

    await waitFor(() => {
      expect(screen.getByTestId('dismiss-0')).toBeDefined();
    });
    mockSubmitCorrection.mockRejectedValueOnce(new Error('Conflict'));
    await user.click(screen.getByTestId('dismiss-0'));
    await screen.findByText('Conflict');

    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Conflict')).toBeNull();
  });
});

describe('Partial detection (degraded pass-1)', () => {
  beforeEach(() => {
    mockReprocessEpisode.mockReset();
    mockReprocessEpisode.mockResolvedValue({});
  });

  it('shows the Partial detection pill and banner when partialDetection is set', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      partialDetection: { reason: 'Ad detection failed: Overloaded', windowsFailed: 2, windowsTotal: 5 },
    }));
    await waitFor(() => {
      expect(screen.getByText('Partial detection')).toBeDefined();
    });
    expect(screen.getByText(
      'The AI detection pass failed during processing. Ads were removed using pattern and cross-fetch evidence only, so some ads may remain.',
    )).toBeDefined();
  });

  it('does not show the pill or banner when partialDetection is null', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [], partialDetection: null }));
    await waitFor(() => {
      expect(screen.getByText('Test Episode')).toBeDefined();
    });
    expect(screen.queryByText('Partial detection')).toBeNull();
    expect(screen.queryByText(/AI detection pass failed/)).toBeNull();
  });

  it('the Re-run detection button fires reprocess with mode llm', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      partialDetection: { reason: 'Ad detection failed: Overloaded', windowsFailed: 2, windowsTotal: 5 },
    }));
    await waitFor(() => {
      expect(screen.getByText('Partial detection')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Re-run detection' }));
    await waitFor(() => {
      expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'llm');
    });
  });
});

describe('Regenerate Chapters: progress and result feedback', () => {
  beforeEach(() => {
    mockRegenerateChapters.mockReset();
  });

  async function openMenuAndRegenerate(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByText('Test Episode');
    await user.click(screen.getByRole('button', { name: 'Reprocess' }));
    await user.click(screen.getByText('Regenerate Chapters'));
  }

  it('shows the progress text once the menu closes while the call is pending', async () => {
    const user = userEvent.setup();
    let resolveRegenerate: () => void = () => {};
    mockRegenerateChapters.mockImplementation(
      () => new Promise<void>((resolve) => { resolveRegenerate = resolve; }),
    );
    renderDetail(makeEpisode({ pendingReviewMarkers: [], transcriptVttAvailable: true }));

    await openMenuAndRegenerate(user);

    // The menu (and its "Regenerate Chapters" item) unmounted; the progress
    // text renders outside it, so this is the only remaining match.
    expect(await screen.findByText('Regenerating chapters...')).not.toBeNull();

    resolveRegenerate();
  });

  it('shows a confirmation once regeneration succeeds', async () => {
    const user = userEvent.setup();
    mockRegenerateChapters.mockResolvedValue({});
    renderDetail(makeEpisode({ pendingReviewMarkers: [], transcriptVttAvailable: true }));

    await openMenuAndRegenerate(user);

    expect(await screen.findByText('Chapters regenerated.')).not.toBeNull();
  });

  it('surfaces the API error message when regeneration fails', async () => {
    const user = userEvent.setup();
    mockRegenerateChapters.mockRejectedValueOnce(new Error('LLM request timed out'));
    renderDetail(makeEpisode({ pendingReviewMarkers: [], transcriptVttAvailable: true }));

    await openMenuAndRegenerate(user);

    expect(await screen.findByText('LLM request timed out')).not.toBeNull();
  });

  it('keeps the dropdown item disabled while regeneration is pending', async () => {
    const user = userEvent.setup();
    let resolveRegenerate: () => void = () => {};
    mockRegenerateChapters.mockImplementation(
      () => new Promise<void>((resolve) => { resolveRegenerate = resolve; }),
    );
    renderDetail(makeEpisode({ pendingReviewMarkers: [], transcriptVttAvailable: true }));

    await openMenuAndRegenerate(user);
    await screen.findByText('Regenerating chapters...');

    // Reopen the menu; a user who reopens it mid-flight should see it's busy.
    await user.click(screen.getByRole('button', { name: 'Reprocess' }));
    const menuItem = screen.getByText('Regenerate Chapters').closest('button');
    expect(menuItem).toHaveProperty('disabled', true);

    resolveRegenerate();
  });
});

// ---- Local feed episode metadata: season/episode seed from the API
// payload, not from parsing the episode id (#625 Task 13 review finding 1)
// ----

// Local-feed episode edit is only rendered when the parent feed is local;
// renderDetail's shared getFeed stub omits feedType, so this describe block
// needs its own render helper that supplies one.
function renderLocalDetail(ep: EpisodeDetailType) {
  const client = makeClient();
  (getEpisode as ReturnType<typeof vi.fn>).mockResolvedValue(ep);
  (getFeed as ReturnType<typeof vi.fn>).mockResolvedValue({
    slug: 'test-feed', title: 'Feed', artworkUrl: null, feedType: 'local',
  });
  return render(
    <QueryClientProvider client={client}>
      <EpisodeDetail />
    </QueryClientProvider>,
  );
}

describe('Local feed episode metadata: season/episode seed from the API payload', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('seeds Season/Episode from seasonNumber/episodeNumber (not the id), and a title-only save posts those seeded values', async () => {
    const user = userEvent.setup();
    // id parses to season=1/episode=1; the payload says season=2/episode=7.
    // Before the fix, get_episode never echoed seasonNumber/episodeNumber,
    // so the form fell back to parsing the id and would show 1/1 here.
    const ep = makeEpisode({
      id: 's01e01',
      seasonNumber: 2,
      episodeNumber: 7,
      pendingReviewMarkers: [],
    });
    mockUpdateLocalEpisode.mockResolvedValue(ep);
    renderLocalDetail(ep);

    await screen.findByText('Edit metadata');

    const seasonInput = await screen.findByLabelText('Season') as HTMLInputElement;
    const episodeInput = screen.getByLabelText('Episode') as HTMLInputElement;
    expect(seasonInput.value).toBe('2');
    expect(episodeInput.value).toBe('7');

    // Title-only edit: season/episode inputs are left untouched.
    const titleInput = screen.getByLabelText('Title') as HTMLInputElement;
    await user.clear(titleInput);
    await user.type(titleInput, 'Renamed Episode');

    await user.click(screen.getByRole('button', { name: /Save metadata/ }));

    await waitFor(() => expect(mockUpdateLocalEpisode).toHaveBeenCalledTimes(1));
    const [, , payload] = mockUpdateLocalEpisode.mock.calls[0] as [string, string, { season?: number; episode?: number; title?: string }];
    // Must echo the payload-seeded values (2/7), not the id-parsed ones (1/1).
    expect(payload.season).toBe(2);
    expect(payload.episode).toBe(7);
    expect(payload.title).toBe('Renamed Episode');
  });
});

// ---- Original-audio player for unprocessed local episodes: the admin
// route (original.mp3) works since 2.93.2, but the detail page showed no
// player until a run completed. ----
describe('Original audio player for unprocessed local episodes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a player sourced from the original-audio route for a discovered local episode', async () => {
    const ep = makeEpisode({
      status: 'discovered', processedAt: null, hasOriginalAudio: true, pendingReviewMarkers: [],
    });
    renderLocalDetail(ep);
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());

    const players = document.querySelectorAll('audio');
    const originalPlayer = Array.from(players).find(
      (el) => el.getAttribute('src') === '/api/v1/feeds/test-feed/episodes/ep-1/original.mp3',
    );
    expect(originalPlayer).toBeDefined();
    expect(screen.getByText(/ad removal hasn't run yet/i)).toBeDefined();
  });

  it('renders no player for a discovered episode on a subscribed (non-local) feed', async () => {
    const ep = makeEpisode({
      status: 'discovered', processedAt: null, hasOriginalAudio: true, pendingReviewMarkers: [],
    });
    renderDetail(ep);
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());

    expect(document.querySelector('audio[src*="original.mp3"]')).toBeNull();
    expect(screen.queryByText(/ad removal hasn't run yet/i)).toBeNull();
  });
});

describe('Process vs Reprocess label (single episode)', () => {
  beforeEach(() => {
    mockSubmitCorrection.mockReset();
    mockReprocessEpisode.mockReset();
  });

  it('labels the action button "Process" for a never-processed (discovered) episode', async () => {
    renderDetail(makeEpisode({ status: 'discovered', processedAt: null, pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.getByRole('button', { name: 'Process' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Reprocess' })).toBeNull();
  });

  it('labels the action button "Process" for a never-processed (pending) episode', async () => {
    renderDetail(makeEpisode({ status: 'pending', processedAt: null, pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.getByRole('button', { name: 'Process' })).toBeDefined();
  });

  it('labels the action button "Process" for a never-processed episode deferred to the offline queue', async () => {
    // Regression case: 'deferred' can happen before an episode's first
    // run too, so status alone can't distinguish this from a
    // reprocess-queued episode -- only processedAt can.
    renderDetail(makeEpisode({ status: 'deferred', processedAt: null, pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.getByRole('button', { name: 'Process' })).toBeDefined();
  });

  it('labels the action button "Reprocess" for an already-processed (completed) episode', async () => {
    renderDetail(makeEpisode({ status: 'completed', pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.getByRole('button', { name: 'Reprocess' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Process' })).toBeNull();
  });

  it('labels the action button "Reprocess" for a failed episode (it was processed once)', async () => {
    renderDetail(makeEpisode({ status: 'failed', pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.getByRole('button', { name: 'Reprocess' })).toBeDefined();
  });

  it('labels the action button "Reprocess" for a reprocess-queued episode (status back to pending, processedAt still set)', async () => {
    // Regression case (round-2 review finding): a reprocess request flips
    // status back to pending/processing but reset_episode_for_reprocess
    // never clears processedAt, so the episode has been processed before
    // and must keep reading "Reprocess" throughout that window, not
    // regress to "Process".
    renderDetail(makeEpisode({ status: 'pending', processedAt: '2025-06-01T00:00:00Z', pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    expect(screen.getByRole('button', { name: 'Reprocess' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Process' })).toBeNull();
  });

  it('carries the "Process" label into the dropdown menu\'s first entry for a never-processed episode', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ status: 'discovered', processedAt: null, pendingReviewMarkers: [] }));
    await waitFor(() => expect(screen.getByText('Test Episode')).toBeDefined());
    await user.click(screen.getByRole('button', { name: 'Process' }));
    expect(screen.getAllByText('Process').length).toBeGreaterThanOrEqual(2);
  });
});
