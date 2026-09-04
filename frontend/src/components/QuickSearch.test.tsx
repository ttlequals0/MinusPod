import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import QuickSearch, { useQuickSearchHotkey } from './QuickSearch';

const mockQuickSearch = vi.fn();
vi.mock('../api/quickSearch', () => ({
  quickSearch: (...a: unknown[]) => mockQuickSearch(...a),
}));
const mockNavigate = vi.fn();
vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => mockNavigate,
}));

function renderPalette(seed = 'ba', onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <QuickSearch seed={seed} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('QuickSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockQuickSearch.mockResolvedValue({
      query: 'ba',
      feeds: [{ slug: 'example-podcast', title: 'The Daily Tech Show' }],
      episodes: [{ feedSlug: 'example-podcast', feedTitle: 'The Daily Tech Show',
        episodeId: 'a1b2c3d4e5f6', title: 'Batteries again', status: 'pending', publishDate: null }],
    });
  });

  it('seeds the input and lists grouped results', async () => {
    renderPalette();
    expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('ba');
    await waitFor(() => expect(screen.getByText('Batteries again')).toBeTruthy());
    expect(screen.getByText('Feeds')).toBeTruthy();
    expect(screen.getByText('Episodes')).toBeTruthy();
    expect(screen.getAllByRole('option')).toHaveLength(2);
  });

  it('arrow keys move the active row and Escape closes', async () => {
    const onClose = vi.fn();
    renderPalette('ba', onClose);
    await waitFor(() => screen.getByText('Batteries again'));
    await userEvent.keyboard('{ArrowDown}');
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true');
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  it('ignores arrows and Enter with no rows, then selects index 1 once results arrive', async () => {
    mockQuickSearch.mockResolvedValueOnce({ query: 'ba', feeds: [], episodes: [] });
    renderPalette();
    await waitFor(() => screen.getByText('No feed or episode titles match.'));
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(mockNavigate).not.toHaveBeenCalled();
    await userEvent.type(screen.getByRole('combobox'), 'n');
    await waitFor(() => screen.getByText('Batteries again'));
    await userEvent.keyboard('{ArrowDown}');
    expect(screen.getAllByRole('option')[1].getAttribute('aria-selected')).toBe('true');
    await userEvent.keyboard('{Enter}');
    expect(mockNavigate).toHaveBeenCalledWith('/feeds/example-podcast/episodes/a1b2c3d4e5f6');
  });

  it('offers a transcript search link', async () => {
    renderPalette();
    await waitFor(() => screen.getByText('Batteries again'));
    const link = screen.getByRole('link', { name: /search transcripts/i });
    expect(link.getAttribute('href')).toBe('/search?q=ba');
  });

  it('links to plain /search with an empty query', () => {
    renderPalette('');
    const link = screen.getByRole('link', { name: 'Open full search' });
    expect(link.getAttribute('href')).toBe('/search');
    expect(mockQuickSearch).not.toHaveBeenCalled();
  });

  it('renders nothing when seed is null', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <QuickSearch seed={null} onClose={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('useQuickSearchHotkey', () => {
  it('opens on a printable key outside fields, not inside an input, and empty on Ctrl+K', async () => {
    const onOpen = vi.fn();
    renderHook(() => useQuickSearchHotkey(onOpen));
    const input = document.createElement('input');
    document.body.appendChild(input);

    document.body.focus();
    await userEvent.keyboard('b');
    expect(onOpen).toHaveBeenCalledWith('b');

    onOpen.mockClear();
    input.focus();
    await userEvent.keyboard('b');
    expect(onOpen).not.toHaveBeenCalled();

    document.body.focus();
    await userEvent.keyboard('{Control>}k{/Control}');
    expect(onOpen).toHaveBeenCalledWith('');
    input.remove();
  });
});
