import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import PatternsPage from './PatternsPage';

const mockGetPatterns = vi.fn().mockResolvedValue([]);
const mockGetDetections = vi.fn().mockResolvedValue({
  detections: [], total: 0, page: 1, totalPages: 1, limit: 20,
});

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  getPatterns: (...a: unknown[]) => mockGetPatterns(...a),
  getPatternStats: vi.fn().mockResolvedValue({
    total: 0, active: 0, inactive: 0,
    by_scope: { global: 0, network: 0, podcast: 0 },
    no_sponsor: 0, never_matched: 0, stale_count: 0,
    high_false_positive_count: 0,
    stale_patterns: [], no_sponsor_patterns: [], high_false_positive_patterns: [],
  }),
  getMergeSuggestions: vi.fn().mockResolvedValue([]),
}));
vi.mock('../api/detections', () => ({
  getDetections: (...a: unknown[]) => mockGetDetections(...a),
}));
vi.mock('../api/community', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/community')>()),
  getCommunitySyncStatus: vi.fn().mockResolvedValue({
    enabled: false, cron: '', lastRun: null, lastError: null,
    manifestVersion: null, lastSummary: null,
  }),
}));

function renderPage(initialEntry = '/patterns') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <PatternsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('PatternsPage tabs', () => {
  it('shows the patterns tab by default', async () => {
    renderPage();
    const tab = await screen.findByRole('tab', { name: 'Patterns' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });

  it('switches to the ad review tab on click', async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Ad Review' }));
    expect(mockGetDetections).toHaveBeenCalled();
  });

  it('opens the ad review tab from the URL', async () => {
    renderPage('/patterns?tab=ad-review');
    const tab = await screen.findByRole('tab', { name: 'Ad Review' });
    expect(tab.getAttribute('aria-selected')).toBe('true');
  });
});
