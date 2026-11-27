import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import AdReviewTab from './AdReviewTab';
import type { ReviewDetection } from '../../api/detections';

const mockGetDetections = vi.fn();
const mockClosePending = vi.fn();
const mockGetSettings = vi.fn();
// Deliberately unsorted: the dropdown must sort by title.
const mockGetFeedsResponse = vi.fn().mockResolvedValue({
  feeds: [
    { slug: 'feed-b', title: 'Feed B' },
    { slug: 'feed-a', title: 'Feed A' },
  ],
  lastRefreshCompletedAt: null,
});
const COUNTS = {
  total: 1, needsReview: 1, pending: 0, rejected: 1,
  accepted: 0, confirmed: 0, dismissed: 0,
};
const mockSubmitCorrection = vi.fn().mockResolvedValue(undefined);
const mockReprocess = vi.fn().mockResolvedValue({ message: '', mode: 'recut' });

vi.mock('../../api/detections', () => ({
  getDetections: (...a: unknown[]) => mockGetDetections(...a),
  closePendingDetections: (...a: unknown[]) => mockClosePending(...a),
}));
vi.mock('../../api/settings', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/settings')>()),
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
}));
vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  getFeedsResponse: (...a: unknown[]) => mockGetFeedsResponse(...a),
  // The real feedsQueryOptions captured the unmocked getFeedsResponse at
  // module load; rebuild it against the mock.
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
// AdReviewModal renders WaveSurfer; its own behavior is covered by its use on
// the episode page. Here only AdReviewTab's submit mapping matters.
const modalProps: { onSubmit?: (s: { kind: string; sponsor?: string }) => void } = {};
vi.mock('../../components/AdReviewModal', () => ({
  default: (props: typeof modalProps) => {
    modalProps.onSubmit = props.onSubmit;
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
    feedSlug: 'feed-a', feedTitle: 'Feed A',
    episodeId: 'ep-1', episodeTitle: 'Episode One',
    publishDate: '2026-07-01T00:00:00Z', hasOriginalAudio: true,
    episodeDuration: 3600,
    processedUrl: '/episodes/feed-a/ep-1.mp3',
    start: 100, end: 130, confidence: 0.4,
    sponsor: 'Acme', reason: 'sponsor read',
    patternId: null, detectionStage: 'first_pass',
    category: null, actionApplied: null,
    reviewerVerdict: null, reviewerOriginalStart: null, reviewerOriginalEnd: null,
    reviewerMoved: false, holdReason: null,
    status: 'rejected', resolution: 'unresolved',
    ...over,
  };
}

function renderTab(entry = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <AdReviewTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  modalProps.onSubmit = undefined;
  mockGetDetections.mockResolvedValue({
    detections: [detection()], total: 1, page: 1, totalPages: 1, limit: 20,
    counts: COUNTS,
  });
  mockSubmitCorrection.mockClear();
  mockReprocess.mockClear();
});

describe('AdReviewTab', () => {
  it('renders a detection row with episode link and status', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    const rows = screen.getByTestId('detections-rows');
    const link = within(rows).getByRole('link', { name: 'Episode One' });
    expect(link.getAttribute('href')).toBe('/feeds/feed-a/episodes/ep-1');
    // Scope to the row: "Feed A" also appears as a filter <option> label.
    const row = link.closest('[data-testid="detection-row"]') as HTMLElement;
    expect(within(row).getByText('Feed A')).toBeTruthy();
    expect(within(row).getByText('Not cut')).toBeTruthy();
    // No chip for an undecided row; a recorded decision still gets one.
    expect(within(row).queryByText('Not reviewed')).toBeNull();
    // The second meta line carries what the old table columns did.
    expect(within(row).getByText(/2026/)).toBeTruthy();
    expect(within(row).getByText(/\(30s\)/)).toBeTruthy();
    expect(within(row).getByText('40% confidence')).toBeTruthy();
    expect(within(row).getByText('Acme')).toBeTruthy();
  });

  it('requests needs_review by default', async () => {
    renderTab();
    await waitFor(() => expect(mockGetDetections).toHaveBeenCalled());
    expect(mockGetDetections.mock.calls[0][0]).toMatchObject({
      status: 'needs_review', page: 1, sort: 'date', order: 'desc',
    });
  });

  it('changes the status filter and resets to page 1', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.selectOptions(screen.getByLabelText('Status'), 'all');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        status: 'all', page: 1,
      });
    });
  });

  it('filters by podcast', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.selectOptions(await screen.findByLabelText('Podcast'), 'feed-b');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        feed: 'feed-b',
      });
    });
  });

  it('renders dynamic rows on desktop, not a fixed-width table', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    // The fixed 9-column table forced horizontal scroll below 68rem; the
    // row layout flexes at any width.
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.getByTestId('detections-rows')).toBeTruthy();
  });

  it('renders the stats card and sorts the podcast dropdown by title', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(screen.getByRole('region', { name: 'Detection statistics' })).toBeTruthy();
    expect(screen.getAllByText('Needs review').length).toBeTruthy();
    const select = await screen.findByLabelText('Podcast') as HTMLSelectElement;
    const labels = [...select.options].map((o) => o.text);
    expect(labels).toEqual(['All podcasts', 'Feed A', 'Feed B']);
  });

  it('renders a mobile card variant for each detection', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    const cards = screen.getByTestId('detections-cards');
    expect(within(cards).getByRole('link', { name: 'Episode One' })).toBeTruthy();
    expect(within(cards).getByText('Acme')).toBeTruthy();
    const row = within(cards).getByTestId('detection-actions-row');
    const buttons = within(row).getAllByRole('button');
    expect(buttons.map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual(
      ['Play this ad', 'Confirm', 'Not ad', 'Split', 'Edit']);
    // Buttons stay together; the category select comes last in the same wrapping row.
    expect(row.className).toContain('flex-wrap');
    const category = within(cards).getByRole('combobox', { name: /^Category for/ });
    expect(row.contains(category)).toBe(true);
    expect(buttons[4].compareDocumentPosition(category) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(category.className).toContain('flex-1');
    expect(category.className).toContain('sm:w-44');
    expect(category.title).toBe('Uncategorized');
    expect(row.style.getPropertyValue('--btn-row')).toBe('252px');
    // Below sm the buttons are 44px square icons (label in the accessible name and title).
    for (const b of buttons) {
      expect(b.className).toMatch(/h-11|max-sm:h-11/);
      expect(b.className).toMatch(/w-11|max-sm:w-11/);
    }
    for (const name of ['Confirm', 'Not ad', 'Split', 'Edit']) {
      expect(within(row).getByRole('button', { name }).getAttribute('title')).toBe(name);
    }
  });

  it('keeps the row controls in fixed columns with the category last', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    const row = within(screen.getByTestId('detections-rows')).getByTestId('detection-row');
    const names = within(row).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent);
    expect(names).toEqual(['Play this ad', 'Confirm', 'Not ad', 'Split', 'Edit']);
    const category = within(row).getByRole('combobox', { name: /^Category for/ });
    expect(within(row).getByRole('button', { name: 'Edit' }).compareDocumentPosition(category)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId('detections-rows').style.getPropertyValue('--cols').split(' ')).toHaveLength(7);
  });

  it('names card buttons for a sponsor and a non-ad category marker', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [
        detection({ episodeId: 'sp', episodeTitle: 'Sponsor Episode', category: 'sponsor' }),
        detection({ episodeId: 'sf', episodeTitle: 'Self Promo Episode', category: 'self_promo' }),
      ],
      total: 2, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab();
    await screen.findAllByRole('link', { name: 'Sponsor Episode' });
    const [sponsor, selfPromo] = within(screen.getByTestId('detections-cards')).getAllByTestId('detection-actions-row');
    const names = (r: HTMLElement) => within(r).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent);
    expect(names(sponsor)).toEqual(['Play this ad', 'Confirm', 'Not ad', 'Split', 'Edit']);
    expect(names(selfPromo)).toEqual(['Play this ad', 'Confirm self-promo', 'Keep', 'Split', 'Edit']);
    const confirm = within(selfPromo).getByRole('button', { name: 'Confirm self-promo' });
    expect(confirm.getAttribute('title')).toBe('Confirm self-promo');
    // Not ad is an X; Keep is a different icon so it does not read as a rejection.
    expect(within(sponsor).getByRole('button', { name: 'Not ad' }).querySelector('svg.lucide-x')).not.toBeNull();
    const keep = within(selfPromo).getByRole('button', { name: 'Keep' });
    expect(keep.querySelector('svg.lucide-x')).toBeNull();
    expect(keep.querySelector('svg.lucide-undo-2')).not.toBeNull();
    expect(confirm.textContent).toBe('Confirm');
  });

  it('Split opens the modal and defers the recut to the Apply bar', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(within(screen.getByTestId('detections-cards')).getByRole('button', { name: 'Split' }));
    await user.click(screen.getByRole('button', { name: 'finish split' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'finish split' })).toBeNull());
    expect(mockReprocess).not.toHaveBeenCalled();
  });

  it('sorts from the filter-bar sort control and resets direction on column change', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(screen.getByRole('button', { name: 'Newest, switch to oldest' }));
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({ order: 'asc' });
    });
    await user.selectOptions(screen.getByLabelText('Sort by'), 'confidence');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        sort: 'confidence', order: 'desc', page: 1,
      });
    });
  });

  it('shows the empty state when nothing needs review', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [], total: 0, page: 1, totalPages: 1, limit: 20,
      counts: { ...COUNTS, total: 0, needsReview: 0, rejected: 0 },
    });
    renderTab();
    expect(await screen.findByText('No detections need review.')).toBeTruthy();
  });
});

describe('AdReviewTab row actions', () => {
  it('approve submits a confirm correction without recutting on the spot', async () => {
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Confirm' }))[0]);
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledOnce());
    expect(mockSubmitCorrection.mock.calls[0][0]).toBe('feed-a');
    expect(mockSubmitCorrection.mock.calls[0][1]).toBe('ep-1');
    expect(mockSubmitCorrection.mock.calls[0][2]).toMatchObject({
      type: 'confirm',
      original_ad: { start: 100, end: 130 },
    });
    // Review is bulk work: the server stamps the episode and the Apply bar
    // cuts it once, so a decision must not start its own recut.
    expect(mockReprocess).not.toHaveBeenCalled();
  });

  it('a confirm from the review modal forwards the typed sponsor', async () => {
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Edit' }))[0]);
    await waitFor(() => expect(modalProps.onSubmit).toBeDefined());
    act(() => modalProps.onSubmit!({ kind: 'confirm', sponsor: 'Globex' }));
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledOnce());
    expect(mockSubmitCorrection.mock.calls[0][2]).toMatchObject({
      type: 'confirm',
      original_ad: { start: 100, end: 130, sponsor: 'Acme' },
      sponsor: 'Globex',
    });
  });

  it('dismiss submits a reject correction with no recut', async () => {
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Not ad' }))[0]);
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledOnce());
    expect(mockSubmitCorrection.mock.calls[0][2]).toMatchObject({
      type: 'reject',
    });
    expect(mockReprocess).not.toHaveBeenCalled();
  });

  it('resolved rows show no approve or dismiss buttons', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ resolution: 'dismissed' })],
      total: 1, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Not ad' })).toBeNull();
    // Edit (and the audition play button) must survive resolution in both the desktop row and the card.
    const cards = screen.getByTestId('detections-cards');
    expect(within(cards).getByRole('button', { name: 'Edit' })).toBeTruthy();
    const rows = screen.getByTestId('detections-rows');
    expect(within(rows).getByRole('button', { name: 'Edit' })).toBeTruthy();
  });

  it('hides the play button when original audio is gone', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ hasOriginalAudio: false })],
      total: 1, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(screen.queryByRole('button', { name: /play/i })).toBeNull();
  });

  it('shows error banner when correction fails and does not call reprocess', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockSubmitCorrection.mockRejectedValueOnce(new Error('boom'));
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Confirm' }))[0]);
    expect(await screen.findByText('boom')).toBeTruthy();
    expect(mockReprocess).not.toHaveBeenCalled();
    errSpy.mockRestore();
  });
});

describe('AdReviewTab category filter', () => {
  it('sends the selected category and resets to page 1', async () => {
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

  it('offers an uncategorized option distinct from all categories', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    const select = screen.getByLabelText('Category') as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values[0]).toBe('');
    expect(values).toContain('none');
  });

  it('omits the category param when set to all categories', async () => {
    renderTab();
    await waitFor(() => expect(mockGetDetections).toHaveBeenCalled());
    expect(mockGetDetections.mock.calls[0][0].category).toBeUndefined();
  });
});

describe('AdReviewTab reviewer filter', () => {
  it('omits the reviewer param by default and sends the selection with page 1', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(mockGetDetections.mock.calls[0][0].reviewer).toBeUndefined();
    await user.selectOptions(screen.getByLabelText('Reviewer'), 'adjusted');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        reviewer: 'adjusted', page: 1,
      });
    });
  });

  it('badges a reviewer-adjusted row with the original span in both layouts', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({
        reviewerVerdict: 'adjust', reviewerOriginalStart: 98, reviewerOriginalEnd: 131,
        reviewerMoved: true,
      })],
      total: 1, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    for (const id of ['detections-rows', 'detections-cards']) {
      const badge = within(screen.getByTestId(id)).getByText('Adjusted');
      expect(badge.getAttribute('title')).toBe('Reviewer moved this from 1:38 - 2:11');
    }
  });

  it('shows no badge for a confirmed, unreviewed, or unmoved adjust row', async () => {
    mockGetDetections.mockResolvedValue({
      // The third row is a held contradiction: verdict kept, no move recorded.
      detections: [
        detection({ reviewerVerdict: 'confirmed' }),
        detection(),
        detection({ reviewerVerdict: 'adjust' }),
      ],
      total: 3, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    // Scope to the lists: the Reviewer select also offers an "Adjusted" option.
    for (const id of ['detections-rows', 'detections-cards']) {
      expect(within(screen.getByTestId(id)).queryByText('Adjusted')).toBeNull();
    }
  });
});

describe('AdReviewTab loading placeholder', () => {
  it('shows row skeletons while the detections query is pending', async () => {
    mockGetDetections.mockReturnValueOnce(new Promise(() => {}));
    renderTab();
    expect(await screen.findByTestId('skeleton-rows')).toBeTruthy();
  });

  it('drops the skeletons once the detections land', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
  });
});

describe('AdReviewTab hold-reason filter', () => {
  it('shows the select for needs_review only, with per-reason counts', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection()], total: 1, page: 1, totalPages: 1, limit: 20,
      counts: { ...COUNTS, pendingByHoldReason: { verification_miss: 3 } },
    });
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    const select = screen.getByLabelText('Hold reason');
    expect(within(select).getByRole('option', { name: 'Verification catch (3)' })).toBeTruthy();
    expect(within(select).getByRole('option', { name: 'Over max duration' })).toBeTruthy();
    expect(within(screen.getByLabelText('Status')).queryByRole('option', { name: 'Pending review' })).toBeNull();
    await user.selectOptions(screen.getByLabelText('Status'), 'accepted');
    expect(screen.queryByLabelText('Hold reason')).toBeNull();
  });

  it('sends the selected reason with page 1 and drops it when the status leaves pending', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(mockGetDetections.mock.calls[0][0].holdReason).toBeUndefined();
    await user.selectOptions(screen.getByLabelText('Hold reason'), 'max_duration');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        holdReason: 'max_duration', page: 1,
      });
    });
    await user.selectOptions(screen.getByLabelText('Status'), 'all');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({ status: 'all' });
    });
    expect(mockGetDetections.mock.lastCall?.[0].holdReason).toBeUndefined();
  });
});

describe('AdReviewTab list state in the URL', () => {
  it('requests the page named by ?page=2', async () => {
    renderTab('/?page=2');
    await screen.findAllByRole('link', { name: 'Episode One' });
    expect(mockGetDetections.mock.calls.some(([p]) => p.page === 2)).toBe(true);
  });

  it('resets to page 1 when a filter changes', async () => {
    const user = userEvent.setup();
    renderTab('/?page=2');
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.selectOptions(screen.getByLabelText('Status'), 'all');
    await waitFor(() => expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({ page: 1, status: 'all' }));
  });
});

describe('AdReviewTab: auto-closed rows', () => {
  it('notes the closing reason and keeps Confirm and Not ad', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ autoClosed: true, closedHoldReason: 'max_duration' })],
      total: 1, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab('/?status=rejected');
    const row = (await screen.findByTestId('detection-row'));
    expect(within(row).getByTestId('auto-closed-note').textContent).toBe('Closed automatically: Over max duration');
    expect(within(row).getByRole('button', { name: /^Confirm/ })).toBeTruthy();
    expect(within(row).getByRole('button', { name: 'Not ad' })).toBeTruthy();
  });

  it('shows no note without the fields', async () => {
    renderTab('/?status=rejected');
    await screen.findByTestId('detection-row');
    expect(screen.queryByTestId('auto-closed-note')).toBeNull();
  });

  it('falls back to a bare note when the reason is missing', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ autoClosed: true, closedHoldReason: null })],
      total: 1, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab('/?status=rejected');
    expect((await screen.findByTestId('detection-row')).textContent).toContain('Closed automatically');
  });
});

describe('AdReviewTab: Close all needing review', () => {
  const BUTTON = 'Close all needing review';
  const listWith = (pending: number, feedTotal = 0) => (p: { status?: string }) => Promise.resolve({
    detections: [detection()], total: p.status === 'pending' ? feedTotal : 1, page: 1, totalPages: 1, limit: 20,
    counts: { ...COUNTS, pending },
  });

  beforeEach(() => {
    mockClosePending.mockReset();
    mockGetDetections.mockImplementation(listWith(3));
    mockGetSettings.mockResolvedValue({ holdForReview: { value: false, isDefault: true } });
  });

  it('is hidden while the global setting holds and no feed is selected', async () => {
    mockGetSettings.mockResolvedValue({ holdForReview: { value: true, isDefault: false } });
    renderTab();
    await screen.findByTestId('detection-row');
    await waitFor(() => expect(mockGetSettings).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: BUTTON })).toBeNull();
  });

  it('shows for a feed overridden off even when the global setting holds', async () => {
    mockGetSettings.mockResolvedValue({ holdForReview: { value: true, isDefault: false } });
    mockGetFeedsResponse.mockResolvedValueOnce({
      feeds: [{ slug: 'feed-a', title: 'Feed A', holdForReview: false }], lastRefreshCompletedAt: null,
    });
    mockGetDetections.mockImplementation(listWith(3, 2));
    renderTab('/?feed=feed-a');
    expect(await screen.findByRole('button', { name: BUTTON })).toBeTruthy();
  });

  it('is hidden for a feed overridden on even when the global setting is off', async () => {
    mockGetFeedsResponse.mockResolvedValueOnce({
      feeds: [{ slug: 'feed-a', title: 'Feed A', holdForReview: true }], lastRefreshCompletedAt: null,
    });
    mockGetDetections.mockImplementation(listWith(3, 2));
    renderTab('/?feed=feed-a');
    await screen.findByTestId('detection-row');
    await waitFor(() => expect(mockGetFeedsResponse).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: BUTTON })).toBeNull();
  });

  it('is hidden when nothing is pending', async () => {
    mockGetDetections.mockImplementation(listWith(0));
    renderTab();
    await screen.findByTestId('detection-row');
    expect(screen.queryByRole('button', { name: BUTTON })).toBeNull();
  });

  it('uses the list counts and makes no extra query without a feed filter', async () => {
    renderTab();
    expect(await screen.findByText('3 detections need review')).toBeTruthy();
    expect(mockGetDetections.mock.calls.some(([p]) => p.limit === 1)).toBe(false);
  });

  it('confirms with an upper bound, closes every feed, and reports the result', async () => {
    mockClosePending.mockResolvedValue({ closed: 3, episodes: 2 });
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: BUTTON }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Close all detections needing review?')).toBeTruthy();
    expect(within(dialog).getByText(/Up to 3 detections across all feeds will be closed and listed as Not cut/)).toBeTruthy();
    expect(within(dialog).getByText(/Detections you already approved, and those on feeds that hold for review, stay open/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: BUTTON }));
    await waitFor(() => expect(mockClosePending).toHaveBeenCalledWith(undefined));
    expect((await screen.findByRole('status')).textContent).toBe('3 detections closed in 2 episodes.');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('adds the skipped episodes to the result', async () => {
    mockClosePending.mockResolvedValue({ closed: 1, episodes: 1, skipped: 2 });
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: BUTTON }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: BUTTON }));
    expect((await screen.findByRole('status')).textContent).toBe(
      '1 detection closed in 1 episode. 2 episodes were skipped because they changed during the close.',
    );
  });

  it('uses the singular for one skipped episode', async () => {
    mockClosePending.mockResolvedValue({ closed: 1, episodes: 1, skipped: 1 });
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: BUTTON }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: BUTTON }));
    expect((await screen.findByRole('status')).textContent).toContain('1 episode was skipped because it changed during the close.');
  });

  it('scopes to the filtered feed with that feed\'s own pending total', async () => {
    mockGetDetections.mockImplementation(listWith(3000, 2));
    mockClosePending.mockResolvedValue({ closed: 1, episodes: 1 });
    renderTab('/?feed=feed-a');
    await userEvent.click(await screen.findByRole('button', { name: BUTTON }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Up to 2 detections in this feed will be closed/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: BUTTON }));
    await waitFor(() => expect(mockClosePending).toHaveBeenCalledWith('feed-a'));
  });

  it('disables the confirm button while the request runs', async () => {
    mockClosePending.mockReturnValue(new Promise(() => {}));
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: BUTTON }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: BUTTON }));
    const busy = await within(dialog).findByRole('button', { name: 'Closing...' });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the server message on failure and clears it on the next attempt', async () => {
    mockClosePending.mockRejectedValueOnce(new Error('Close failed on the server'));
    renderTab();
    await userEvent.click(await screen.findByRole('button', { name: BUTTON }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: BUTTON }));
    expect((await screen.findByRole('alert')).textContent).toBe('Close failed on the server');
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: BUTTON }));
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
