import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CutSummary, ReviewDetection } from '../../api/detections';
import DetectedAdsTab from './DetectedAdsTab';

const mockGetDetections = vi.fn();
const mockGetFeedsResponse = vi.fn().mockResolvedValue({ feeds: [] });
const mockSubmitCorrection = vi.fn().mockResolvedValue({});
const mockReprocess = vi.fn().mockResolvedValue({});

vi.mock('../../api/detections', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/detections')>()),
  getDetections: (...a: unknown[]) => mockGetDetections(...a),
}));
vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  getFeedsResponse: (...a: unknown[]) => mockGetFeedsResponse(...a),
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: (...a: unknown[]) => mockGetFeedsResponse(...a),
  },
  reprocessEpisode: (...a: unknown[]) => mockReprocess(...a),
}));
vi.mock('../../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/patterns')>()),
  submitCorrection: (...a: unknown[]) => mockSubmitCorrection(...a),
}));
// AdReviewModal renders WaveSurfer; its behavior is covered by its own tests.
// The stub records its props so the wiring can be asserted.
const reviewModalProps = vi.hoisted(
  () => ({ current: null as Record<string, unknown> | null }));
vi.mock('../../components/AdReviewModal', () => ({
  default: (props: Record<string, unknown>) => {
    reviewModalProps.current = props;
    return null;
  },
}));
vi.mock('../../components/SplitMarkerModal', () => ({
  default: ({ onSplit }: {
    onSplit: (r: { markerCount: number; patternIds: number[] }) => void;
  }) => (
    <button onClick={() => onSplit({ markerCount: 2, patternIds: [1, 2] })}>
      finish split
    </button>
  ),
}));

function detection(over: Partial<ReviewDetection> = {}): ReviewDetection {
  return {
    feedSlug: 'example-podcast', feedTitle: 'The Daily Tech Show',
    episodeId: 'a1b2c3d4e5f6', episodeTitle: 'Episode One',
    publishDate: '2026-07-01T00:00:00Z', hasOriginalAudio: true,
    episodeDuration: 3600,
    processedUrl: '/episodes/example-podcast/a1b2c3d4e5f6.mp3',
    start: 100, end: 130, confidence: 0.9,
    sponsor: 'Acme', reason: 'sponsor read',
    patternId: null, detectionStage: 'first_pass',
    category: 'sponsor', actionApplied: 'remove',
    status: 'accepted', resolution: 'unresolved',
    ...over,
  };
}

const SUMMARY: CutSummary = {
  count: 4,
  durationSeconds: 7200,
  byCategory: {
    sponsor: 2, cross_promo: 1, none: 1,
    self_promo: 0, interaction: 0, intro: 0, outro: 0, recap: 0,
  },
  distinctSponsors: 3,
  distinctPodcasts: 2,
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DetectedAdsTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  reviewModalProps.current = null;
  mockGetFeedsResponse.mockResolvedValue({ feeds: [] });
  mockGetDetections.mockResolvedValue({
    detections: [detection()],
    total: 1, page: 1, totalPages: 1, limit: 20,
    counts: {
      total: 1, needsReview: 0, pending: 0, rejected: 0,
      accepted: 1, confirmed: 0, dismissed: 0,
    },
    cutSummary: SUMMARY,
  });
});

describe('DetectedAdsTab', () => {
  it('requests only detections that were cut', async () => {
    renderTab();
    await waitFor(() => expect(mockGetDetections).toHaveBeenCalled());
    expect(mockGetDetections.mock.calls[0][0]).toMatchObject({
      status: 'accepted', page: 1, sort: 'date', order: 'desc',
    });
  });

  it('leads with the total time cut', async () => {
    renderTab();
    expect(await screen.findByText('Time cut')).toBeTruthy();
    expect(screen.getByText('2.0h')).toBeTruthy();
    expect(screen.getByText('Detections')).toBeTruthy();
    expect(screen.getByText('Sponsors')).toBeTruthy();
    expect(screen.getByText('Podcasts')).toBeTruthy();
  });

  it('orders the category breakdown by count and omits empty ones', async () => {
    renderTab();
    await screen.findByText('By category');
    const block = screen.getByText('By category').parentElement as HTMLElement;
    const labels = within(block).getAllByText(
      /Sponsor|Cross-promo|Uncategorized|Self-promo|Interaction|Intro|Outro|Recap/,
    ).map((el) => el.textContent);
    expect(labels).toEqual(['Sponsor', 'Cross-promo', 'Uncategorized']);
  });

  it('offers no Confirm action, since these ads were already cut', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(screen.queryByRole('button', { name: 'Confirm ad' })).toBeNull();
    expect(screen.queryAllByRole('button', { name: 'Not an ad' })).not.toHaveLength(0);
    expect(screen.queryAllByRole('button', { name: 'Edit' })).not.toHaveLength(0);
  });

  it('rejecting a cut ad triggers a recut so the audio comes back', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(screen.getAllByRole('button', { name: 'Not an ad' })[0]);
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalled());
    expect(mockSubmitCorrection.mock.calls[0][2]).toMatchObject({ type: 'reject' });
    await waitFor(() => expect(mockReprocess).toHaveBeenCalledWith(
      'example-podcast', 'a1b2c3d4e5f6', 'recut'));
  });

  it('sends the selected category', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.selectOptions(screen.getByLabelText('Category'), 'cross_promo');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        category: 'cross_promo', page: 1,
      });
    });
  });

  it('distinguishes an empty library from empty filter results', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [], total: 0, page: 1, totalPages: 1, limit: 20,
      counts: {
        total: 0, needsReview: 0, pending: 0, rejected: 0,
        accepted: 0, confirmed: 0, dismissed: 0,
      },
      cutSummary: { ...SUMMARY, count: 0, durationSeconds: 0 },
    });
    renderTab();
    expect(await screen.findByText('No ads have been cut yet.')).toBeTruthy();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText('Category'), 'outro');
    expect(await screen.findByText('No cut ads match the current filters.')).toBeTruthy();
  });

  it('surfaces a load failure', async () => {
    mockGetDetections.mockRejectedValue(new Error('boom'));
    renderTab();
    expect(await screen.findByText('Failed to load detected ads.')).toBeTruthy();
  });

  it('a finished split names the ad count and triggers the recut', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(screen.getAllByRole('button', { name: 'Split' })[0]);
    await user.click(screen.getByRole('button', { name: 'finish split' }));
    expect(await screen.findByText('Split into 2 ads.')).toBeTruthy();
    await waitFor(() => expect(mockReprocess).toHaveBeenCalledWith(
      'example-podcast', 'a1b2c3d4e5f6', 'recut'));
  });

  it('a split without retained audio defers the recut to the next reprocess', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ hasOriginalAudio: false })],
      total: 1, page: 1, totalPages: 1, limit: 20,
      counts: {
        total: 1, needsReview: 0, pending: 0, rejected: 0,
        accepted: 1, confirmed: 0, dismissed: 0,
      },
      cutSummary: SUMMARY,
    });
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(screen.getAllByRole('button', { name: 'Split' })[0]);
    await user.click(screen.getByRole('button', { name: 'finish split' }));
    expect(await screen.findByText(
      'Split into 2 ads. The recut applies on the next reprocess.')).toBeTruthy();
    expect(mockReprocess).not.toHaveBeenCalled();
  });

  it('opens the edit modal with Confirm hidden and a split handler', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    await waitFor(() => expect(reviewModalProps.current).not.toBeNull());
    expect(reviewModalProps.current?.hideConfirm).toBe(true);
    expect(typeof reviewModalProps.current?.onSplitSaved).toBe('function');
  });

  it('shows a beeped marker as beeped rather than as a plain cut', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ actionApplied: 'beep' })],
      total: 1, page: 1, totalPages: 1, limit: 20,
      counts: {
        total: 1, needsReview: 0, pending: 0, rejected: 0,
        accepted: 1, confirmed: 0, dismissed: 0,
      },
      cutSummary: SUMMARY,
    });
    renderTab();
    expect(await screen.findAllByText('Beeped')).not.toHaveLength(0);
  });
});
