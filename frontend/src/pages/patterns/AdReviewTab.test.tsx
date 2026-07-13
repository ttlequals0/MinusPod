import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import AdReviewTab from './AdReviewTab';
import type { ReviewDetection } from '../../api/detections';

const mockGetDetections = vi.fn();
// Deliberately unsorted: the dropdown must sort by title.
const mockGetFeeds = vi.fn().mockResolvedValue([
  { slug: 'feed-b', title: 'Feed B' },
  { slug: 'feed-a', title: 'Feed A' },
]);
const COUNTS = {
  total: 1, needsReview: 1, pending: 0, rejected: 1,
  accepted: 0, confirmed: 0, dismissed: 0,
};
const mockSubmitCorrection = vi.fn().mockResolvedValue(undefined);
const mockReprocess = vi.fn().mockResolvedValue({ message: '', mode: 'recut' });

vi.mock('../../api/detections', () => ({
  getDetections: (...a: unknown[]) => mockGetDetections(...a),
}));
vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  getFeeds: (...a: unknown[]) => mockGetFeeds(...a),
  reprocessEpisode: (...a: unknown[]) => mockReprocess(...a),
}));
vi.mock('../../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/patterns')>()),
  submitCorrection: (...a: unknown[]) => mockSubmitCorrection(...a),
}));
// AdReviewModal renders WaveSurfer; its own behavior is covered by its use on
// the episode page. Here only AdReviewTab's submit mapping matters.
vi.mock('../../components/AdReviewModal', () => ({ default: () => null }));

function detection(over: Partial<ReviewDetection> = {}): ReviewDetection {
  return {
    feedSlug: 'feed-a', feedTitle: 'Feed A',
    episodeId: 'ep-1', episodeTitle: 'Episode One',
    publishDate: '2026-07-01T00:00:00Z', hasOriginalAudio: true,
    processedUrl: '/episodes/feed-a/ep-1.mp3',
    start: 100, end: 130, confidence: 0.4,
    sponsor: 'Acme', reason: 'sponsor read',
    patternId: null, detectionStage: 'first_pass',
    status: 'rejected', resolution: 'unresolved',
    ...over,
  };
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AdReviewTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
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
    expect(within(row).getByText('Unresolved')).toBeTruthy();
    // The second meta line carries what the old table columns did.
    expect(within(row).getByText(/2026/)).toBeTruthy();
    expect(within(row).getByText(/\(30s\)/)).toBeTruthy();
    expect(within(row).getByText('conf 0.40')).toBeTruthy();
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
    expect(screen.getByText('Detection Statistics')).toBeTruthy();
    expect(screen.getByText('Needs Review')).toBeTruthy();
    const select = await screen.findByLabelText('Podcast') as HTMLSelectElement;
    const labels = [...select.options].map((o) => o.text);
    expect(labels).toEqual(['All podcasts', 'Feed A', 'Feed B']);
  });

  it('renders a mobile card variant for each detection', async () => {
    renderTab();
    await screen.findAllByRole('link', { name: 'Episode One' });
    const cards = screen.getByTestId('detections-cards');
    expect(within(cards).getByRole('link', { name: 'Episode One' })).toBeTruthy();
    expect(within(cards).getByRole('button', { name: 'Confirm ad' })).toBeTruthy();
    expect(within(cards).getByText('Acme')).toBeTruthy();
  });

  it('sorts from the filter-bar sort control and resets direction on column change', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findAllByRole('link', { name: 'Episode One' });
    await user.click(screen.getByRole('button', { name: 'Switch to ascending order' }));
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({ order: 'asc' });
    });
    await user.selectOptions(screen.getByLabelText('Sort'), 'confidence');
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
  it('approve submits a confirm correction and triggers recut', async () => {
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Confirm ad' }))[0]);
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledOnce());
    expect(mockSubmitCorrection.mock.calls[0][0]).toBe('feed-a');
    expect(mockSubmitCorrection.mock.calls[0][1]).toBe('ep-1');
    expect(mockSubmitCorrection.mock.calls[0][2]).toMatchObject({
      type: 'confirm',
      original_ad: { start: 100, end: 130 },
    });
    await waitFor(() =>
      expect(mockReprocess).toHaveBeenCalledWith('feed-a', 'ep-1', 'recut'));
  });

  it('approve without original audio skips the recut', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [detection({ hasOriginalAudio: false })],
      total: 1, page: 1, totalPages: 1, limit: 20, counts: COUNTS,
    });
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Confirm ad' }))[0]);
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledOnce());
    expect(mockReprocess).not.toHaveBeenCalled();
  });

  it('dismiss submits a reject correction with no recut', async () => {
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Not an ad' }))[0]);
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
    expect(screen.queryByRole('button', { name: 'Confirm ad' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Not an ad' })).toBeNull();
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
    await user.click((await screen.findAllByRole('button', { name: 'Confirm ad' }))[0]);
    expect(await screen.findByText('Failed to save correction. Try again.')).toBeTruthy();
    expect(mockReprocess).not.toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it('shows recut-failure banner when correction succeeds but reprocess fails', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockReprocess.mockRejectedValueOnce(new Error('recut boom'));
    renderTab();
    const user = userEvent.setup();
    await user.click((await screen.findAllByRole('button', { name: 'Confirm ad' }))[0]);
    expect(
      await screen.findByText(
        'Confirmed, but the recut did not start. The cut applies on the next reprocess.',
      ),
    ).toBeTruthy();
    errSpy.mockRestore();
  });
});
