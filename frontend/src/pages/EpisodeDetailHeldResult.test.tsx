/** Held-for-review result state, footer button and overlap warnings. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EpisodeDetail from './EpisodeDetail';
import type { EpisodeDetail as EpisodeDetailType } from '../api/types';

// Bounds the AdReviewModal stub's adjust button submits; set per test.
const adjustBounds = vi.hoisted(() => ({ start: 0, end: 0 }));

// react-router stubs
vi.mock('react-router', () => ({
  useParams: () => ({ slug: 'test-feed', episodeId: 'ep-1' }),
  useSearchParams: () => [new URLSearchParams(), () => {}],
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
      kind: 'confirm' | 'reject' | 'adjust' | 'recategorize';
      adjustedStart?: number;
      adjustedEnd?: number;
      category?: string | null;
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
      <button
        data-testid="modal-recategorize"
        onClick={() => onSubmit({ kind: 'recategorize', category: 'self_promo' })}
      >
        recategorize
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
  formatConfidenceTitle: () => undefined,
}));

// Mutable mutation stubs; reassigned per test.
const mockSubmitCorrection = vi.fn();
const mockReprocessEpisode = vi.fn();

vi.mock('../api/feeds', () => ({
  getEpisode: vi.fn(),
  getFeed: vi.fn(),
  getOriginalSegments: vi.fn(),
  getFinalSegments: vi.fn(),
  reprocessEpisode: (...args: unknown[]) => mockReprocessEpisode(...args),
  regenerateChapters: vi.fn(),
  episodeOriginalUrl: (slug: string, episodeId: string) =>
    `/api/v1/feeds/${slug}/episodes/${episodeId}/original.mp3`,
  downloadEpisodeAudio: vi.fn(),
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

const approvedTrimmed = {
  ...heldMarker,
  start: 130,
  end: 300,
  approved: true,
  reviewer_original_start: 120,
  reviewer_original_end: 360,
};
const second = { ...heldMarker, start: 600, end: 700 };

describe('Held result state', () => {
  it('approved trimmed row shows the new span, the original and not cut yet', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [approvedTrimmed, second] }));
    const line = await screen.findByTestId('held-result-0');
    expect(line.textContent).toContain('Confirmed 2:10 - 5:00 (was 2:00 - 6:00). Not cut yet.');
  });

  it('approved untrimmed row shows not cut yet', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, approved: true }, second] }));
    const line = await screen.findByTestId('held-result-0');
    expect(line.textContent).toContain('Confirmed 2:00 - 6:00. Not cut yet.');
    expect(line.textContent).not.toContain('was');
    expect(screen.queryByTestId('held-result-1')).toBeNull();
  });

  it('footer recut button is hidden with nothing confirmed', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker, second] }));
    await screen.findByTestId('held-for-review-section');
    expect(screen.queryByTestId('apply-approved-recut')).toBeNull();
  });

  it('footer Cut is enabled and counts confirmed rows', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, approved: true }, second] }));
    const btn = await screen.findByTestId('apply-approved-recut') as HTMLButtonElement;
    expect(btn.textContent).toBe('Apply 1 confirmed and recut');
    expect(btn.disabled).toBe(false);
  });

  it('one-tap row button reads Confirm and recut', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker] }));
    expect((await screen.findByTestId('approve-recut-0')).textContent).toBe('Confirm and recut');
  });
});

describe('Held overlap warnings', () => {
  it('warns on two overlapping held rows, naming the other span', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [
      { ...heldMarker, start: 100, end: 400 },
      { ...heldMarker, start: 300, end: 700 },
    ] }));
    const w0 = await screen.findByTestId('overlap-warning-0');
    const w1 = screen.getByTestId('overlap-warning-1');
    expect(w0.textContent).toContain('Overlaps the held span 5:00 - 11:40.');
    expect(w1.textContent).toContain('Overlaps the held span 1:40 - 6:40.');
  });

  it('same-audio pair shows deciding one decides both', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [
      { ...heldMarker, start: 840, end: 1230 },
      { ...heldMarker, start: 909, end: 1229 },
    ] }));
    const w0 = await screen.findByTestId('overlap-warning-0');
    expect(w0.textContent).toBe('Same audio as the held span 15:09 - 20:29. Deciding one decides both.');
    expect(screen.getByTestId('overlap-warning-1').textContent).toContain('Same audio as the held span 14:00 - 20:30.');
  });

  it('no warning for disjoint rows', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker, second] }));
    await screen.findByTestId('held-for-review-section');
    expect(screen.queryByTestId('overlap-warning-0')).toBeNull();
    expect(screen.queryByTestId('overlap-warning-1')).toBeNull();
  });

  it('no warning for overlap under 1 s', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [
      { ...heldMarker, start: 100, end: 200 },
      { ...heldMarker, start: 199.5, end: 300 },
    ] }));
    await screen.findByTestId('held-for-review-section');
    expect(screen.queryByTestId('overlap-warning-0')).toBeNull();
  });

  it('shows no button for overlap rows', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [
      { ...heldMarker, start: 100, end: 400 },
      { ...heldMarker, start: 300, end: 700 },
    ] }));
    const section = within(await screen.findByTestId('held-for-review-section'));
    expect(section.queryByText('Leave the other in')).toBeNull();
    expect(screen.queryByTestId('drop-overlap-0')).toBeNull();
  });
});

describe('Held row with an adjustment that is not approved (old backend)', () => {
  const adjusted = {
    id: 7,
    correction_type: 'boundary_adjustment' as const,
    original_bounds: { start: 120, end: 360 },
    corrected_bounds: { start: 130, end: 300 },
    created_at: '2026-01-01T00:00:00Z',
  };

  it('reads Adjusted with the span and not cut yet, and offers reject', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker, second], corrections: [adjusted] }));
    const line = await screen.findByTestId('held-result-0');
    expect(line.textContent).toContain('Adjusted to 2:10 - 5:00. Not cut yet.');
    expect(screen.queryByTestId('recut-now-0')).toBeNull();
    expect(screen.getByTestId('dismiss-0')).toBeTruthy();
    const row0 = screen.getByTestId('held-for-review-section').querySelectorAll('.space-y-3 > div')[0] as HTMLElement;
    expect(within(row0).getByText('Adjusted')).toBeTruthy();
  });

  it('counts in the footer cut button', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker, second], corrections: [adjusted] }));
    const btn = await screen.findByTestId('apply-approved-recut') as HTMLButtonElement;
    expect(btn.textContent).toBe('Apply 1 confirmed and recut');
    expect(btn.disabled).toBe(false);
  });

  it('a rejected row keeps its Not ad badge and no controls', async () => {
    const rejected = { ...adjusted, id: 8, correction_type: 'false_positive' as const, corrected_bounds: undefined };
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker, second], corrections: [rejected] }));
    await screen.findByTestId('held-for-review-section');
    const row0 = screen.getByTestId('held-for-review-section').querySelectorAll('.space-y-3 > div')[0] as HTMLElement;
    expect(within(row0).getByText('Not ad')).toBeTruthy();
    expect(screen.queryByTestId('held-result-0')).toBeNull();
    expect(screen.queryByTestId('open-editor-held-0')).toBeNull();
  });
});

describe('Held row explains itself in visible text', () => {
  it('shows the hold explanation as a sentence, same text as the tooltip', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [heldMarker, second] }));
    const why = await screen.findByTestId('held-why-0');
    expect(why.textContent).toBe(
      'The reviewer disagreed with the detected boundaries',
    );
  });
});

describe('Apply confirmed and recut', () => {
  it('starts a recut', async () => {
    const user = userEvent.setup();
    renderDetail(makeEpisode({ pendingReviewMarkers: [{ ...heldMarker, approved: true }, second] }));
    await user.click(await screen.findByTestId('apply-approved-recut'));
    await waitFor(() =>
      expect(mockReprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut'));
    expect(screen.queryByTestId('recut-now-0')).toBeNull();
  });
});
