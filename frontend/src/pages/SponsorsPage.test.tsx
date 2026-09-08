/**
 * Loading-state test for the Sponsors page: the wait shows content-shaped
 * placeholders rather than a page spinner.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SponsorsPage from './SponsorsPage';

const mockGetSponsors = vi.fn();

vi.mock('../api/sponsors', () => ({
  getSponsors: (...a: unknown[]) => mockGetSponsors(...a),
  deleteSponsor: vi.fn(),
}));

vi.mock('../api/community', () => ({
  getTagVocabulary: vi.fn().mockResolvedValue({
    vocabulary_version: 1, all_tags: [], podcast_genres: [],
    sponsor_industries: [], special_tags: [],
  }),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SponsorsPage />
    </QueryClientProvider>,
  );
}

describe('SponsorsPage loading placeholder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSponsors.mockResolvedValue([]);
  });

  it('shows header and row skeletons while the sponsor query is pending', () => {
    mockGetSponsors.mockReturnValueOnce(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('skeleton-page-header')).toBeTruthy();
    expect(screen.getByTestId('skeleton-rows')).toBeTruthy();
  });

  it('drops the skeletons once the sponsors land', async () => {
    renderPage();
    await screen.findByPlaceholderText('Search by name, alias, category...');
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
  });
});
