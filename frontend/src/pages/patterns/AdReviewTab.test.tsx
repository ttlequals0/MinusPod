import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import AdReviewTab from './AdReviewTab';
import type { ReviewDetection } from '../../api/detections';

const mockGetDetections = vi.fn();
const mockGetFeeds = vi.fn().mockResolvedValue([
  { slug: 'feed-a', title: 'Feed A' },
  { slug: 'feed-b', title: 'Feed B' },
]);

vi.mock('../../api/detections', () => ({
  getDetections: (...a: unknown[]) => mockGetDetections(...a),
}));
vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  getFeeds: (...a: unknown[]) => mockGetFeeds(...a),
}));

function detection(over: Partial<ReviewDetection> = {}): ReviewDetection {
  return {
    feedSlug: 'feed-a', feedTitle: 'Feed A',
    episodeId: 'ep-1', episodeTitle: 'Episode One',
    publishDate: '2026-07-01T00:00:00Z', hasOriginalAudio: true,
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
  });
});

describe('AdReviewTab', () => {
  it('renders a detection row with episode link and status', async () => {
    renderTab();
    const link = await screen.findByRole('link', { name: 'Episode One' });
    expect(link.getAttribute('href')).toBe('/feeds/feed-a/episodes/ep-1');
    // Scope to the row: "Feed A" also appears as a filter <option> label.
    const row = link.closest('tr')!;
    expect(within(row).getByText('Feed A')).toBeTruthy();
    expect(within(row).getByText('Rejected')).toBeTruthy();
    expect(within(row).getByText('Unresolved')).toBeTruthy();
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
    await screen.findByRole('link', { name: 'Episode One' });
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
    await screen.findByRole('link', { name: 'Episode One' });
    await user.selectOptions(await screen.findByLabelText('Podcast'), 'feed-b');
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        feed: 'feed-b',
      });
    });
  });

  it('toggles sort direction when the active sort column is clicked', async () => {
    renderTab();
    const user = userEvent.setup();
    await screen.findByRole('link', { name: 'Episode One' });
    await user.click(screen.getByRole('button', { name: /Published/ }));
    await waitFor(() => {
      expect(mockGetDetections.mock.lastCall?.[0]).toMatchObject({
        sort: 'date', order: 'asc',
      });
    });
  });

  it('shows the empty state when nothing needs review', async () => {
    mockGetDetections.mockResolvedValue({
      detections: [], total: 0, page: 1, totalPages: 1, limit: 20,
    });
    renderTab();
    expect(await screen.findByText('No detections need review.')).toBeTruthy();
  });
});
