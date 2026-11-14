import { MemoryRouter } from 'react-router';
/**
 * Loading-state test for the Sponsors page: the wait shows content-shaped
 * placeholders rather than a page spinner.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Sponsor } from '../api/types';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SponsorsSection } from './SponsorsPage';

const mockGetSponsors = vi.fn();

vi.mock('../api/sponsors', () => ({
  getSponsors: (...a: unknown[]) => mockGetSponsors(...a),
  deleteSponsor: vi.fn(),
  addSponsor: vi.fn(),
  updateSponsor: vi.fn(),
}));

vi.mock('../api/community', () => ({
  getTagVocabulary: vi.fn().mockResolvedValue({
    vocabulary_version: 1, all_tags: [], podcast_genres: [],
    sponsor_industries: [], special_tags: [],
  }),
  updateSponsorTags: vi.fn(),
}));

function renderPage(entry = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}><SponsorsSection /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SponsorsPage loading placeholder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSponsors.mockResolvedValue([]);
  });

  it('shows filter-card and row skeletons while the sponsor query is pending', () => {
    mockGetSponsors.mockReturnValueOnce(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId('skeleton-filter-card')).toBeTruthy();
    expect(screen.getByTestId('skeleton-rows')).toBeTruthy();
    expect(screen.queryByTestId('skeleton-page-header')).toBeNull();
  });

  it('drops the skeletons once the sponsors land', async () => {
    renderPage();
    await screen.findByPlaceholderText('Search by name, alias, category...');
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
    expect(screen.queryByTestId('skeleton-filter-card')).toBeNull();
  });
});

describe('SponsorsPage list state in the URL', () => {
  const sponsor = (n: number): Sponsor => ({
    id: n, name: `Sponsor ${String(n).padStart(2, '0')}`, aliases: [], category: null,
    segment_category: null, common_ctas: [], tags: [], is_active: true, pattern_count: 0,
    last_matched_at: null, created_at: '2026-01-01T00:00:00Z',
  });

  beforeEach(() => {
    mockGetSponsors.mockResolvedValue(Array.from({ length: 25 }, (_, i) => sponsor(i + 1)));
  });

  it('shows the second page for ?page=2', async () => {
    renderPage('/?page=2');
    expect((await screen.findAllByText('Sponsor 25')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Sponsor 01')).toBeNull();
  });

  it('returns to the first page when a filter changes', async () => {
    renderPage('/?page=2');
    await screen.findAllByText('Sponsor 25');
    await userEvent.click(screen.getByRole('checkbox', { name: /show inactive/i }));
    expect((await screen.findAllByText('Sponsor 01')).length).toBeGreaterThan(0);
  });

  it('returns to the first page when the search applies', async () => {
    renderPage('/?page=2');
    await screen.findAllByText('Sponsor 25');
    await userEvent.type(screen.getByPlaceholderText('Search by name, alias, category...'), 'Sponsor 0');
    await waitFor(() => expect(screen.getAllByText('Sponsor 01').length).toBeGreaterThan(0));
    expect(screen.queryByText('Sponsor 25')).toBeNull();
  });

  it('restores the sort from the URL', async () => {
    renderPage('/?sort=name&dir=desc');
    await screen.findAllByText('Sponsor 25');
    expect(screen.queryByText('Sponsor 01')).toBeNull();
  });
});

describe('SponsorsPage card layout', () => {
  it('offers the table sort fields and the category and created date on each card', async () => {
    mockGetSponsors.mockResolvedValue([{
      id: 1, name: 'Acme', aliases: [], category: 'Consumer goods', segment_category: null, common_ctas: [],
      tags: [], is_active: true, pattern_count: 3, last_matched_at: null, created_at: '2026-01-01T00:00:00Z',
    }]);
    renderPage('/?sort=created_at&dir=asc');
    await screen.findAllByText('Acme');
    const sort = screen.getByLabelText('Sort by') as HTMLSelectElement;
    expect([...sort.options].map((o) => o.text)).toEqual(['Name', 'Category', 'Patterns', 'Created', 'Last matched']);
    expect(sort.value).toBe('created_at');
    expect(screen.getAllByText('Consumer goods').length).toBeGreaterThan(0);
    expect(screen.getByText(/^created /)).toBeTruthy();
  });
});
