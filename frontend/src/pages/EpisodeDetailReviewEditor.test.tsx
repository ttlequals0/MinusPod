/**
 * Component tests for the "Open in editor" flow on Held for Review and
 * Detections Not Cut rows (issue #563 option 3).
 *
 * Covers:
 *   - Open-editor button renders on held/rejected rows when original audio
 *     is retained; hidden when it is gone.
 *   - Clicking mounts AdReviewModal with the marker bounds; reviewer
 *     proposals seed correctedBounds, absent proposals leave it null.
 *   - onSubmit mapping: adjust -> confirm + adjusted bounds (clamped to the
 *     backend's +/-0.5s tolerance window), confirm -> plain confirm,
 *     reject -> reject. Modal unmounts after submit.
 *   - One-tap recut parity: a held-row confirm that completes the review
 *     set chains a recut; rejected-row confirms never do.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EpisodeDetail from './EpisodeDetail';
import type { EpisodeDetail as EpisodeDetailType } from '../api/types';

// Bounds the AdReviewModal stub's adjust button submits; set per test.
const adjustBounds = vi.hoisted(() => ({ start: 0, end: 0 }));

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
vi.mock('../components/AdReviewModal', () => ({
  default: ({ item, onSubmit, onClose, boundsWindow }: {
    item: {
      start: number;
      end: number;
      correctedBounds: { start: number; end: number } | null;
    };
    onSubmit: (s: {
      kind: 'confirm' | 'reject' | 'adjust';
      adjustedStart?: number;
      adjustedEnd?: number;
    }) => void;
    onClose: () => void;
    boundsWindow?: { min: number; max: number };
  }) => (
    <div
      data-testid="ad-review-modal"
      data-item-start={item.start}
      data-item-end={item.end}
      data-item-corrected-start={item.correctedBounds?.start ?? ''}
      data-item-corrected-end={item.correctedBounds?.end ?? ''}
      data-bounds-min={boundsWindow?.min ?? ''}
      data-bounds-max={boundsWindow?.max ?? ''}
    >
      <button data-testid="modal-confirm" onClick={() => onSubmit({ kind: 'confirm' })}>
        confirm
      </button>
      <button
        data-testid="modal-adjust"
        onClick={() => onSubmit({
          kind: 'adjust',
          adjustedStart: adjustBounds.start,
          adjustedEnd: adjustBounds.end,
        })}
      >
        adjust
      </button>
      <button data-testid="modal-reject" onClick={() => onSubmit({ kind: 'reject' })}>
        reject
      </button>
      <button data-testid="modal-close" onClick={onClose}>
        close
      </button>
    </div>
  ),
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

vi.mock('../api/feeds', () => ({
  getEpisode: vi.fn(),
  getFeed: vi.fn(),
  getOriginalTranscript: vi.fn(),
  reprocessEpisode: (...args: unknown[]) => mockReprocessEpisode(...args),
  regenerateChapters: vi.fn(),
  episodeOriginalUrl: (slug: string, episodeId: string) =>
    `/api/v1/feeds/${slug}/episodes/${episodeId}/original.mp3`,
}));

vi.mock('../api/patterns', () => ({
  submitCorrection: (...args: unknown[]) => mockSubmitCorrection(...args),
}));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

const heldMarker = {
  start: 120,
  end: 360,
  confidence: 0.95,
  reason: 'sponsor match',
  held_for_review: true,
  hold_reason: 'reviewer_contradiction' as const,
};

const heldMarkerWithProposal = {
  ...heldMarker,
  reviewer_proposed_start: 130,
  reviewer_proposed_end: 300,
};

const rejectedMarker = {
  start: 700,
  end: 760,
  confidence: 0.6,
  reason: 'validator reject',
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

import { getEpisode, getFeed } from '../api/feeds';

function renderDetail(ep: EpisodeDetailType) {
  const client = makeClient();
  (getEpisode as ReturnType<typeof vi.fn>).mockResolvedValue(ep);
  (getFeed as ReturnType<typeof vi.fn>).mockResolvedValue({ slug: 'test-feed', title: 'Feed', artworkUrl: null });
  return render(
    <QueryClientProvider client={client}>
      <EpisodeDetail />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockSubmitCorrection.mockReset();
  mockReprocessEpisode.mockReset();
  mockSubmitCorrection.mockResolvedValue({});
  mockReprocessEpisode.mockResolvedValue({});
  adjustBounds.start = 0;
  adjustBounds.end = 0;
});

describe('Open in editor: held rows', () => {
  it('opens the modal with marker bounds and reviewer proposals as correctedBounds', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarkerWithProposal] }));

    const btn = await screen.findByTestId('open-editor-held-0');
    await user.click(btn);

    const modal = screen.getByTestId('ad-review-modal');
    expect(modal.getAttribute('data-item-start')).toBe('120');
    expect(modal.getAttribute('data-item-end')).toBe('360');
    expect(modal.getAttribute('data-item-corrected-start')).toBe('130');
    expect(modal.getAttribute('data-item-corrected-end')).toBe('300');
    expect(modal.getAttribute('data-bounds-min')).toBe('119.5');
    expect(modal.getAttribute('data-bounds-max')).toBe('360.5');
  });

  it('leaves correctedBounds null when the marker has no reviewer proposals', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));

    const modal = screen.getByTestId('ad-review-modal');
    expect(modal.getAttribute('data-item-corrected-start')).toBe('');
    expect(modal.getAttribute('data-item-corrected-end')).toBe('');
  });

  it('maps an in-range adjust submit to a trimmed confirm and unmounts the modal', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));
    adjustBounds.start = 125;
    adjustBounds.end = 350;
    await user.click(screen.getByTestId('modal-adjust'));

    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledTimes(1));
    const [slug, episodeId, payload] = mockSubmitCorrection.mock.calls[0] as [
      string, string, { type: string; adjusted_start?: number; adjusted_end?: number },
    ];
    expect(slug).toBe('test-feed');
    expect(episodeId).toBe('ep-1');
    expect(payload.type).toBe('confirm');
    expect(payload.adjusted_start).toBe(125);
    expect(payload.adjusted_end).toBe(350);
    expect(screen.queryByTestId('ad-review-modal')).toBeNull();
  });

  it('clamps out-of-range adjusted bounds to the +/-0.5s tolerance window', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));
    adjustBounds.start = heldMarker.start - 5;
    adjustBounds.end = heldMarker.end + 5;
    await user.click(screen.getByTestId('modal-adjust'));

    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledTimes(1));
    const [, , payload] = mockSubmitCorrection.mock.calls[0] as [
      string, string, { adjusted_start?: number; adjusted_end?: number },
    ];
    expect(payload.adjusted_start).toBe(heldMarker.start - 0.5);
    expect(payload.adjusted_end).toBe(heldMarker.end + 0.5);
  });

  it('maps reject to a reject correction', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));
    await user.click(screen.getByTestId('modal-reject'));

    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledTimes(1));
    const [, , payload] = mockSubmitCorrection.mock.calls[0] as [string, string, { type: string }];
    expect(payload.type).toBe('reject');
    expect(mockReprocessEpisode).not.toHaveBeenCalled();
    expect(screen.queryByTestId('ad-review-modal')).toBeNull();
  });

  it('maps confirm to a plain confirm with no adjusted bounds', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));
    await user.click(screen.getByTestId('modal-confirm'));

    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledTimes(1));
    const [, , payload] = mockSubmitCorrection.mock.calls[0] as [
      string, string, { type: string; adjusted_start?: number; adjusted_end?: number },
    ];
    expect(payload.type).toBe('confirm');
    expect(payload.adjusted_start).toBeUndefined();
    expect(payload.adjusted_end).toBeUndefined();
  });

  it('held confirm that completes the review set chains a one-tap recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));
    await user.click(screen.getByTestId('modal-confirm'));

    await waitFor(() =>
      expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut'));
  });

  it('close button unmounts the modal without submitting', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));

    await user.click(await screen.findByTestId('open-editor-held-0'));
    await user.click(screen.getByTestId('modal-close'));

    expect(screen.queryByTestId('ad-review-modal')).toBeNull();
    expect(mockSubmitCorrection).not.toHaveBeenCalled();
  });
});

describe('Open in editor: rejected rows', () => {
  it('renders the button and maps confirm to a confirm correction without recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({
      pendingReviewMarkers: [],
      rejectedAdMarkers: [rejectedMarker],
    }));

    await user.click(await screen.findByTestId('open-editor-rejected-0'));

    const modal = screen.getByTestId('ad-review-modal');
    expect(modal.getAttribute('data-item-start')).toBe('700');
    await user.click(screen.getByTestId('modal-confirm'));

    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledTimes(1));
    const [, , payload] = mockSubmitCorrection.mock.calls[0] as [string, string, { type: string }];
    expect(payload.type).toBe('confirm');
    expect(mockReprocessEpisode).not.toHaveBeenCalled();
  });
});

describe('Open in editor: no original audio', () => {
  it('hides the button on both row types', async () => {
    renderDetail(makeEpisode({
      hasOriginalAudio: false,
      pendingReviewMarkers: [heldMarker],
      rejectedAdMarkers: [rejectedMarker],
    }));

    await screen.findByTestId('held-for-review-section');
    expect(screen.queryByTestId('open-editor-held-0')).toBeNull();
    expect(screen.queryByTestId('open-editor-rejected-0')).toBeNull();
  });
});

describe('Open in editor: decided rows', () => {
  it('hides the button on an approved held row and a corrected rejected row', async () => {
    renderDetail(makeEpisode({
      pendingReviewMarkers: [{ ...heldMarker, approved: true }],
      rejectedAdMarkers: [rejectedMarker],
      corrections: [{
        id: 1,
        correction_type: 'false_positive' as const,
        original_bounds: { start: rejectedMarker.start, end: rejectedMarker.end },
        created_at: '2026-01-02T00:00:00Z',
      }],
    }));

    await screen.findByTestId('held-for-review-section');
    expect(screen.queryByTestId('open-editor-held-0')).toBeNull();
    expect(screen.queryByTestId('open-editor-rejected-0')).toBeNull();
  });
});
