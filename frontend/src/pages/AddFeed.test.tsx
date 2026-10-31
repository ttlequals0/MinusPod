/**
 * Component tests for AddFeed: the mode toggle, podcast search gating, the
 * local form fields, and the addLocalFeed submit path.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import AddFeed from './AddFeed';

const mockNavigate = vi.fn();

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => mockNavigate,
}));

const mockAddFeed = vi.fn();
const mockAddYoutubeFeed = vi.fn();
const mockAddLocalFeed = vi.fn();
const mockAddRecentsFeed = vi.fn();
const mockUploadFeedArtwork = vi.fn();
const mockImportOpml = vi.fn();
const mockGetFeedsResponse = vi.fn();

vi.mock('../api/feeds', () => ({
  addRecentsFeed: (...a: unknown[]) => mockAddRecentsFeed(...a),
  addFeed: (...a: unknown[]) => mockAddFeed(...a),
  addYoutubeFeed: (...a: unknown[]) => mockAddYoutubeFeed(...a),
  addLocalFeed: (...a: unknown[]) => mockAddLocalFeed(...a),
  uploadFeedArtwork: (...a: unknown[]) => mockUploadFeedArtwork(...a),
  importOpml: (...a: unknown[]) => mockImportOpml(...a),
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: (...a: unknown[]) => mockGetFeedsResponse(...a),
  },
}));

const mockSearchPodcasts = vi.fn();
vi.mock('../api/podcastSearch', () => ({
  searchPodcasts: (...a: unknown[]) => mockSearchPodcasts(...a),
}));

const mockPreviewYouTube = vi.fn();
vi.mock('../api/youtube', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/youtube')>()),
  previewYouTube: (...a: unknown[]) => mockPreviewYouTube(...a),
}));

const mockGetSettings = vi.fn();
vi.mock('../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
}));

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

const defaultSettings = {
  podcastIndexApiKeyConfigured: false,
  podcastSearchProvider: { value: 'itunes', isDefault: true },
  podcastSearchReady: true,
  youtubeEnabled: { value: false, isDefault: true },
};

function renderAddFeed(settings: unknown = defaultSettings) {
  mockGetSettings.mockResolvedValue(settings);
  mockGetFeedsResponse.mockResolvedValue({ feeds: [], lastRefreshCompletedAt: null });
  return render(
    <QueryClientProvider client={makeClient()}>
      <MemoryRouter>
        <AddFeed />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AddFeed: mode toggle', () => {
  it('starts in subscribe mode with the URL input and OPML section visible', async () => {
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeDefined();
    });
    expect(screen.getByText('Import from OPML')).toBeDefined();
    expect(screen.queryByLabelText('Title')).toBeNull();
  });

  it('switches to local mode and hides the OPML section', async () => {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeDefined();
    });

    await user.click(screen.getByRole('button', { name: 'Create local feed' }));

    expect(screen.getByLabelText('Title')).toBeDefined();
    expect(screen.getByLabelText('Slug')).toBeDefined();
    expect(screen.queryByText('Import from OPML')).toBeNull();
    expect(screen.queryByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeNull();
  });
});

describe('AddFeed: podcast search gating', () => {
  it('searches through iTunes with no PodcastIndex credentials and shows no banner', async () => {
    const user = userEvent.setup();
    mockSearchPodcasts.mockResolvedValue([
      { id: 1, title: 'The Daily Tech Show', description: '', artworkUrl: '', feedUrl: 'https://example.com/feed.xml', author: 'Acme', link: '' },
    ]);
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeDefined();
    });
    expect(screen.queryByText('Configure PodcastIndex API credentials')).toBeNull();

    await user.type(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL'), 'daily');

    await waitFor(() => {
      expect(mockSearchPodcasts).toHaveBeenCalledWith('daily', expect.any(AbortSignal));
    });
    await waitFor(() => {
      expect(screen.getByText('The Daily Tech Show')).toBeDefined();
    });
  });

  it('does not search when the input is a URL', async () => {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeDefined();
    });

    await user.type(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL'), 'https://example.com/feed.xml');

    await new Promise((r) => setTimeout(r, 500));
    expect(mockSearchPodcasts).not.toHaveBeenCalled();
  });

  it('falls back to the URL-only label until settings report a provider', async () => {
    renderAddFeed(new Promise(() => {}));
    expect(screen.getByLabelText('RSS or YouTube URL')).toBeDefined();
    expect(screen.queryByText('Configure PodcastIndex API credentials')).toBeNull();
  });

  it('shows a banner and does not search when PodcastIndex is selected but not ready', async () => {
    const user = userEvent.setup();
    renderAddFeed({
      podcastIndexApiKeyConfigured: false,
      podcastSearchProvider: { value: 'podcastindex', isDefault: false },
      podcastSearchReady: false,
    });
    await waitFor(() => {
      expect(screen.getByText(/PodcastIndex is selected but its credentials are missing/)).toBeDefined();
    });

    await user.type(screen.getByLabelText('RSS or YouTube URL'), 'daily');
    await new Promise((r) => setTimeout(r, 500));
    expect(mockSearchPodcasts).not.toHaveBeenCalled();
  });
});

describe('AddFeed: local feed form', () => {
  async function openLocalMode() {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Create local feed' }));
    return user;
  }

  it('auto-fills the slug from the title until the slug is edited by hand', async () => {
    const user = await openLocalMode();
    const titleInput = screen.getByLabelText('Title') as HTMLInputElement;
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement;

    await user.type(titleInput, 'My Archive Show');
    expect(slugInput.value).toBe('my-archive-show');

    await user.clear(slugInput);
    await user.type(slugInput, 'custom-slug');
    await user.type(titleInput, '!');
    expect(slugInput.value).toBe('custom-slug');
  });

  it('submits addLocalFeed with feedType local and navigates to the new feed', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show',
      feedType: 'local',
      feedUrl: 'https://minuspod.example.com/feeds/my-archive-show.xml',
      message: 'created',
    });

    const user = await openLocalMode();
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockAddLocalFeed).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'My Archive Show' }),
      );
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', undefined);
    });
    expect(mockUploadFeedArtwork).not.toHaveBeenCalled();
  });

  it('shows the addLocalFeed error message on failure', async () => {
    mockAddLocalFeed.mockRejectedValue(new Error('title is required'));
    const user = await openLocalMode();
    await user.type(screen.getByLabelText('Title'), 'Show');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(screen.getByText('title is required')).toBeDefined();
    });
  });

  it('leaves the slug for the server to derive when the slug field was never edited by hand', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    const user = await openLocalMode();
    // Typing only in Title auto-fills the Slug preview, but the field was
    // never edited directly -- the submitted payload must omit slug so the
    // server's own slugify rules produce the canonical value.
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockAddLocalFeed).toHaveBeenCalledWith(
        expect.objectContaining({ slug: undefined }),
      );
    });
  });

  it('sends the user-edited slug once the slug field has been touched', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'custom-slug', feedType: 'local', feedUrl: 'https://x/custom-slug.xml', message: 'created',
    });
    const user = await openLocalMode();
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    const slugInput = screen.getByLabelText('Slug');
    await user.clear(slugInput);
    await user.type(slugInput, 'custom-slug');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockAddLocalFeed).toHaveBeenCalledWith(
        expect.objectContaining({ slug: 'custom-slug' }),
      );
    });
  });
});

describe('AddFeed: artwork upload after create', () => {
  async function openLocalModeWithArtwork() {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter an RSS or YouTube URL')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Create local feed' }));
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    const file = new File(['data'], 'cover.jpg', { type: 'image/jpeg' });
    await user.upload(screen.getByLabelText('Artwork'), file);
    return user;
  }

  it('still navigates when the artwork upload fails, with a non-blocking notice', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    mockUploadFeedArtwork.mockRejectedValue(new Error('413 Payload Too Large'));

    const user = await openLocalModeWithArtwork();
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockUploadFeedArtwork).toHaveBeenCalledWith('my-archive-show', expect.any(File));
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', {
        state: { notice: 'Feed created. Could not upload artwork. Retry from the feed page.' },
      });
    });
  });

  it('passes the backend artwork warning through as a navigation notice', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    mockUploadFeedArtwork.mockResolvedValue({
      message: 'ok',
      artworkUrl: '/artwork/my-archive-show.jpg',
      warning: 'Image is smaller than the recommended 1400x1400.',
    });

    const user = await openLocalModeWithArtwork();
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', {
        state: { notice: 'Feed created. Image is smaller than the recommended 1400x1400.' },
      });
    });
  });

  it('navigates with no notice when the artwork upload succeeds cleanly', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    mockUploadFeedArtwork.mockResolvedValue({ message: 'ok', artworkUrl: '/artwork/my-archive-show.jpg' });

    const user = await openLocalModeWithArtwork();
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', undefined);
    });
  });
});

describe('Recents feed mode', () => {
  it('creates the recents feed with a title and description', async () => {
    mockAddRecentsFeed.mockResolvedValue({ slug: 'recents', feedType: 'recents', feedUrl: '/recents', message: 'ok' });
    renderAddFeed();
    await userEvent.click(await screen.findByRole('button', { name: 'Create recents feed' }));
    const title = screen.getByLabelText('Title');
    await userEvent.clear(title);
    await userEvent.type(title, 'My Recents');
    await userEvent.type(screen.getByLabelText('Description'), 'New episodes');
    await userEvent.click(screen.getByRole('button', { name: 'Create feed' }));
    await waitFor(() => expect(mockAddRecentsFeed).toHaveBeenCalledWith({ title: 'My Recents', description: 'New episodes' }));
  });

  it('hides the option once a recents feed exists', async () => {
    // A one-shot value wins over the default renderAddFeed installs.
    mockGetFeedsResponse.mockResolvedValueOnce({
      feeds: [{ slug: 'recents', title: 'Recents', feedType: 'recents', sourceUrl: 'recents://', feedUrl: '/recents', episodeCount: 0 }],
      lastRefreshCompletedAt: null,
    });
    renderAddFeed();
    await screen.findByRole('button', { name: 'Subscribe to feed' });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Create recents feed' })).toBeNull());
  });
});

describe('YouTube lookup', () => {
  const INPUT = /search podcasts or enter an RSS or YouTube URL/i;
  const PLAYLIST = 'https://www.youtube.com/playlist?list=PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222';
  const enabled = { ...defaultSettings, youtubeEnabled: { value: true, isDefault: false } };
  const preview = {
    enabled: true,
    source: { kind: 'channel', id: 'UCabcdefghijklmnopqrstuv', title: 'The Daily Tech Show', description: 'desc',
              artworkUrl: null, videoCount: 412, alreadyAdded: null },
    rssMatches: [{ title: 'Daily Tech RSS', feedUrl: 'https://feeds.example.com/dts.xml', artworkUrl: '' }],
  };

  it('relabels the button and shows the preview with an RSS alternative', async () => {
    mockPreviewYouTube.mockResolvedValue(preview);
    mockAddYoutubeFeed.mockResolvedValue({ slug: 'the-daily-tech-show', feedType: 'youtube', feedUrl: 'x' });
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), 'https://www.youtube.com/@dailytech');
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    expect(await screen.findByText('The Daily Tech Show')).toBeDefined();
    expect(screen.getByText('Channel')).toBeDefined();
    expect(screen.getByText(/412 videos/)).toBeDefined();
    expect(screen.getByRole('heading', { name: /or add by rss feed/i })).toBeDefined();
    expect(screen.getByText('Daily Tech RSS')).toBeDefined();
    expect(screen.queryByText(/may not be a podcast rss feed/i)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /add youtube feed/i }));
    await waitFor(() => expect(mockAddYoutubeFeed).toHaveBeenCalledWith({
      sourceKind: 'channel', sourceId: 'UCabcdefghijklmnopqrstuv', title: 'The Daily Tech Show',
      description: 'desc', artworkUrl: null, includeShorts: false, slug: undefined,
      autoProcessOverride: null, maxEpisodes: undefined, onlyExposeProcessedEpisodes: null }));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/feeds/the-daily-tech-show'));
  });

  it('links to the existing feed when the source is already added', async () => {
    mockPreviewYouTube.mockResolvedValue({ ...preview, source: { ...preview.source, alreadyAdded: 'dts' } });
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), PLAYLIST);
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    const link = await screen.findByRole('link', { name: /already added, open feed/i });
    expect(link.getAttribute('href')).toBe('/feeds/dts');
    expect(screen.queryByRole('button', { name: /add youtube feed/i })).toBeNull();
  });

  it('aborts an in-flight lookup when the input changes', async () => {
    let signal: AbortSignal | undefined;
    mockPreviewYouTube.mockImplementation((_url: string, sig: AbortSignal) => {
      signal = sig;
      return new Promise(() => {});
    });
    renderAddFeed(enabled);
    const input = await screen.findByLabelText(INPUT);
    await userEvent.type(input, PLAYLIST);
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal?.aborted).toBe(false);
    await userEvent.type(input, 'x');
    expect(signal?.aborted).toBe(true);
  });

  it('discards a lookup result that arrives after the input changed', async () => {
    let complete!: (value: typeof preview) => void;
    mockPreviewYouTube.mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    renderAddFeed(enabled);
    const input = await screen.findByLabelText(INPUT);
    await userEvent.type(input, PLAYLIST);
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    await userEvent.type(input, 'x');
    await act(async () => complete(preview));
    expect(screen.queryByText('The Daily Tech Show')).toBeNull();
  });

  it('passes the advanced options when adding a YouTube source', async () => {
    mockPreviewYouTube.mockResolvedValue(preview);
    mockAddYoutubeFeed.mockResolvedValue({ slug: 'the-daily-tech-show', feedType: 'youtube', feedUrl: 'x' });
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), PLAYLIST);
    await userEvent.click(screen.getByText('Advanced options'));
    await userEvent.selectOptions(screen.getByLabelText('Auto-process'), 'disable');
    await userEvent.type(screen.getByLabelText('Max episodes in feed'), '50');
    await userEvent.selectOptions(screen.getByLabelText('Only expose processed episodes in feed'), 'enable');
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    await userEvent.click(await screen.findByRole('button', { name: /add youtube feed/i }));
    await waitFor(() => expect(mockAddYoutubeFeed).toHaveBeenCalledWith(expect.objectContaining({
      autoProcessOverride: false, maxEpisodes: 50, onlyExposeProcessedEpisodes: true,
    })));
  });

  it('labels a single video and omits the video count', async () => {
    mockPreviewYouTube.mockResolvedValue({
      enabled: true, rssMatches: [],
      source: { kind: 'video', id: 'abc123def45', title: 'One Video', description: '', author: 'Example Channel',
                artworkUrl: null, videoCount: 1, alreadyAdded: null },
    });
    mockAddYoutubeFeed.mockResolvedValue({ slug: 'one-video', feedType: 'youtube', feedUrl: 'x' });
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), 'https://youtu.be/abc123def45');
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    expect(await screen.findByText('One Video')).toBeDefined();
    expect(screen.getByText('Video')).toBeDefined();
    expect(screen.queryByText(/1 videos/)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /add youtube feed/i }));
    await waitFor(() => expect(mockAddYoutubeFeed).toHaveBeenCalledWith(
      expect.objectContaining({ sourceKind: 'video', sourceId: 'abc123def45' })));
  });

  it('points at Settings when YouTube is disabled', async () => {
    renderAddFeed(defaultSettings);
    const input = await screen.findByLabelText(INPUT);
    await userEvent.type(input, PLAYLIST);
    expect(await screen.findByText(/youtube feeds are turned off/i)).toBeDefined();
    expect(screen.getByRole('link', { name: 'Settings' }).getAttribute('href')).toBe('/settings#youtube');
    expect(input.getAttribute('aria-describedby')).toContain('youtube-disabled');
    expect((screen.getByRole('button', { name: /look up/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(mockPreviewYouTube).not.toHaveBeenCalled();
  });

  it('shows a not-found error from the preview', async () => {
    mockPreviewYouTube.mockRejectedValue(new Error('Playlist not found or private'));
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), PLAYLIST);
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/not found or private/i);
  });

  it('keeps the Add feed button for a plain RSS URL', async () => {
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), 'https://example.com/feed.xml');
    expect(screen.getByRole('button', { name: 'Add feed' })).toBeDefined();
    expect(screen.queryByRole('button', { name: /look up/i })).toBeNull();
  });

  it.each([
    ['https://youtu.be/abc123def45', true],
    ['https://www.youtube.com/watch?v=abc123def45', true],
    ['https://example.com/feed.xml', false],
    ['https://www.youtube.com/@x', true],
    ['https://music.youtube.com/playlist?list=PLabcdefghijkl', true],
    ['PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222', true],
    ['UCabcdefghijklmnopqrstuv', true],
    ['@dailytech', true],
  ])('classifies %s as YouTube input: %s', async (text, expected) => {
    renderAddFeed(enabled);
    await userEvent.type(await screen.findByLabelText(INPUT), text);
    expect(!!screen.queryByRole('button', { name: /look up/i })).toBe(expected);
  });
});
