// Feed page sections are tabs: one visible at a time, hidden panels stay mounted, URL reflects the choice.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import FeedDetail from './FeedDetail';
import type { Feed } from '../api/types';

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => vi.fn(),
}));

vi.mock('./feeds/FeedStatsCards', () => ({ default: () => null }));
vi.mock('./feeds/PodcastAdDistributionPanel', () => ({ default: () => <p>ad distribution body</p> }));
vi.mock('./feeds/CueTemplatesPanel', () => ({ default: () => <p>cue templates body</p> }));
vi.mock('./patterns/PendingRecutsBar', () => ({ PendingRecutsBar: () => null }));
vi.mock('../components/Artwork', () => ({ default: ({ alt }: { alt: string }) => <img alt={alt} /> }));
vi.mock('../components/FeedTagsEditor', () => ({ FeedTagsEditor: () => null }));

const FEED: Feed = {
  slug: 'feed-a', title: 'Feed A', sourceUrl: 'https://feed.example.com/a.xml',
  feedUrl: 'https://feed.example.com/a', episodeCount: 12,
};

vi.mock('../api/feeds', () => ({
  getFeed: () => Promise.resolve(FEED),
  feedsQueryOptions: { queryKey: ['feeds'], queryFn: () => Promise.resolve({ feeds: [FEED], lastRefreshCompletedAt: null }) },
  getEpisodes: () => Promise.resolve({ episodes: [], total: 0 }),
  getNetworks: () => Promise.resolve([]),
  refreshFeed: vi.fn(),
  updateFeed: vi.fn(),
  bulkEpisodeAction: vi.fn(),
  setEpisodesPassthrough: vi.fn(),
  rerenderSegments: vi.fn(),
  deleteFeed: vi.fn(),
  CUE_SCORE_MIN: 0.30,
  CUE_SCORE_MAX: 0.99,
}));
vi.mock('../api/cueTemplates', () => ({ listCueTemplates: () => Promise.resolve([]) }));
vi.mock('../api/settings', () => ({
  getSettings: () => Promise.resolve({}),
  getAudioSettings: () => Promise.resolve({ keepOriginalAudio: true }),
}));

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.search}</output>;
}

function renderAt(entry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['feed', 'feed-a'], FEED);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Where />
        <Routes><Route path="/feeds/:slug" element={<FeedDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const panel = (name: string) => document.getElementById(`feed-sections-panel-${name}`) as HTMLElement;

describe('FeedDetail sections', () => {
  beforeEach(() => localStorage.clear());

  it('opens on Episodes with the episode count and one visible panel', async () => {
    renderAt('/feeds/feed-a');
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['Episodes12', 'Feed settings', 'Ad distribution', 'Audio cues']);
    expect(screen.getByRole('tab', { name: /Episodes/ }).getAttribute('aria-selected')).toBe('true');
    expect(panel('episodes').hidden).toBe(false);
    expect(panel('settings').hidden).toBe(true);
    expect(panel('ads').hidden).toBe(true);
    expect(panel('cues').hidden).toBe(true);
    expect(screen.queryByText('ad distribution body')).toBeNull();
  });

  it('switches sections, writes ?section= and drops it again for Episodes', async () => {
    const user = userEvent.setup();
    renderAt('/feeds/feed-a');
    await user.click(await screen.findByRole('tab', { name: 'Ad distribution' }));
    expect(screen.getByTestId('where').textContent).toBe('?section=ads');
    expect(panel('ads').hidden).toBe(false);
    expect(panel('episodes').hidden).toBe(true);
    expect(within(panel('ads')).getByText('ad distribution body')).toBeTruthy();
    await user.click(screen.getByRole('tab', { name: /Episodes/ }));
    expect(screen.getByTestId('where').textContent).toBe('');
    expect(panel('episodes').hidden).toBe(false);
  });

  it('keeps unsaved Feed settings edits when switching away and back', async () => {
    const user = userEvent.setup();
    renderAt('/feeds/feed-a?section=settings');
    const notes = await screen.findByLabelText('Detection notes');
    await user.type(notes, 'host reads ads early');
    await user.click(screen.getByRole('tab', { name: /Episodes/ }));
    expect(panel('settings').hidden).toBe(true);
    await user.click(screen.getByRole('tab', { name: 'Feed settings' }));
    expect((screen.getByLabelText('Detection notes') as HTMLTextAreaElement).value).toBe('host reads ads early');
  });

  it('opens the right section from a ?section= link and from an old #anchor link', async () => {
    const { unmount } = renderAt('/feeds/feed-a?section=cues');
    expect((await screen.findByRole('tab', { name: 'Audio cues' })).getAttribute('aria-selected')).toBe('true');
    expect(within(panel('cues')).getByText('cue templates body')).toBeTruthy();
    unmount();
    renderAt('/feeds/feed-a#ad-distribution-section');
    expect((await screen.findByRole('tab', { name: 'Ad distribution' })).getAttribute('aria-selected')).toBe('true');
    expect(panel('ads').hidden).toBe(false);
  });

  it('ignores an unknown section value', async () => {
    renderAt('/feeds/feed-a?section=bogus');
    expect((await screen.findByRole('tab', { name: /Episodes/ })).getAttribute('aria-selected')).toBe('true');
  });

  it('keeps only the selected tab in the tab order and moves with arrow keys', async () => {
    const user = userEvent.setup();
    renderAt('/feeds/feed-a');
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1, -1]);
    tabs[0].focus();
    await user.keyboard('{ArrowRight}');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Feed settings' }));
    expect(panel('settings').hidden).toBe(false);
  });

  it('does not put controls of hidden panels in the tab order', async () => {
    renderAt('/feeds/feed-a?section=settings');
    const notes = await screen.findByLabelText('Detection notes');
    expect(notes.closest('[hidden]')).toBeNull();
    const search = screen.getByLabelText('Search episode titles');
    expect(search.closest('[hidden]')).toBe(panel('episodes'));
  });
});
