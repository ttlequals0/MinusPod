/** Held-for-review rows: hold badges, labels and tooltips (A1). */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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

describe('Held rows: hold badge', () => {
  it('renders the humanised reason for an unknown hold reason', async () => {
    renderDetail(makeEpisode({ pendingReviewMarkers: [
      { ...heldMarker, hold_reason: 'some_new_reason' as unknown as typeof heldMarker.hold_reason },
    ] }));
    const section = within(await screen.findByTestId('held-for-review-section'));
    const badge = section.getByText('Some new reason');
    expect(badge.getAttribute('title')).toBe(
      'Held for manual review');
  });

  it('shows the why and what-to-do tooltip on the hold badge', async () => {
    renderDetail(makeEpisode());
    const section = within(await screen.findByTestId('held-for-review-section'));
    const badge = section.getByText('Reviewer contradiction');
    expect(badge.getAttribute('title')).toBe(
      'The reviewer disagreed with the detected boundaries');
  });
});
