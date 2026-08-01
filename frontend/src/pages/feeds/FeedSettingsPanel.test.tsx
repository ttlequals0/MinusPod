/**
 * Component tests for the cross-fetch differential toggle and DAI-likely hint
 * in FeedSettingsPanel.tsx.
 *
 * Covers:
 *   - Toggle renders unchecked when differentialFetchEnabled is unset.
 *   - Enabling fires updateFeed with { differentialFetchEnabled: true }.
 *   - Disabling an enabled feed fires { differentialFetchEnabled: false }.
 *   - DAI-likely badge + hint render only when feed.daiLikely is true.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeedSettingsPanel from './FeedSettingsPanel';
import type { Feed } from '../../api/types';

// CollapsibleSection defaults closed; render children unconditionally.
vi.mock('../../components/CollapsibleSection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components/CollapsibleSection')>();
  return {
    useCollapsibleOpen: actual.useCollapsibleOpen,
    default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  };
});

const mockUpdateFeed = vi.fn();
const mockRerenderSegments = vi.fn();

vi.mock('../../api/feeds', () => ({
  getNetworks: vi.fn().mockResolvedValue([]),
  updateFeed: (...args: unknown[]) => mockUpdateFeed(...args),
  rerenderSegments: (...args: unknown[]) => mockRerenderSegments(...args),
  CUE_SCORE_MIN: 0.30,
  CUE_SCORE_MAX: 0.99,
}));

const mockGetSettings = vi.fn();

vi.mock('../../api/settings', () => ({
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
}));

// FeedTagsEditor queries api/community internally; not under test here.
vi.mock('../../components/FeedTagsEditor', () => ({
  FeedTagsEditor: () => null,
}));

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    slug: 'test-feed',
    title: 'Test Feed',
    sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://example.com/modified.xml',
    episodeCount: 3,
    ...overrides,
  };
}

mockGetSettings.mockResolvedValue({});

function renderPanel(feed: Feed) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FeedSettingsPanel feed={feed} slug={feed.slug} />
    </QueryClientProvider>,
  );
}

const SELECT_NAME = 'Fetch each episode twice to find inserted ads';

describe('FeedSettingsPanel processing mode preset', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders one select with the four presets', () => {
    renderPanel(makeFeed({ processingMode: 'standard' }));
    const select = screen.getByLabelText(/processing mode/i);
    expect(select).toBeDefined();
    for (const label of ['Standard', 'Keep content only', 'Skip ad detection', 'Pass-through']) {
      expect(screen.getByRole('option', { name: new RegExp(label, 'i') })).toBeDefined();
    }
  });

  it('sends processingMode on change', async () => {
    renderPanel(makeFeed({ processingMode: 'standard' }));
    await userEvent.selectOptions(screen.getByLabelText(/processing mode/i), 'passthrough');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { processingMode: 'passthrough' });
  });

  it('does not render the legacy toggles', () => {
    renderPanel(makeFeed({ processingMode: 'standard' }));
    expect(screen.queryByRole('switch', { name: 'Skip ad detection' })).toBeNull();
    expect(screen.queryByRole('switch', { name: 'Serve episodes untouched' })).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Detection' })).toBeNull();
  });
});

describe('FeedSettingsPanel cross-fetch differential control', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders Auto when differentialFetchEnabled is unset', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('selecting On fires updateFeed with differentialFetchEnabled true', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(screen.getByRole('combobox', { name: SELECT_NAME }), 'true');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: true });
  });

  it('selecting Off fires updateFeed with differentialFetchEnabled false', async () => {
    renderPanel(makeFeed({ differentialFetchEnabled: true }));
    const select = screen.getByRole('combobox', { name: SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('true');
    await userEvent.selectOptions(select, 'false');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: false });
  });

  it('selecting Auto restores null so DAI feeds auto-enable again', async () => {
    renderPanel(makeFeed({ differentialFetchEnabled: false }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: SELECT_NAME }), '');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: null });
  });

  it('shows the effective state resolved by the server', () => {
    const { unmount } = renderPanel(makeFeed({ differentialFetchEffective: true }));
    expect(screen.getByText('Runs on this feed')).toBeDefined();
    unmount();
    renderPanel(makeFeed({ differentialFetchEffective: false }));
    expect(screen.getByText('Not running')).toBeDefined();
  });

  it('shows the DAI-likely badge only when daiLikely is true', () => {
    const { unmount } = renderPanel(makeFeed({ daiLikely: true }));
    expect(screen.getByText('DAI likely')).toBeDefined();
    unmount();
    renderPanel(makeFeed());
    expect(screen.queryByText('DAI likely')).toBeNull();
  });
});

describe('FeedSettingsPanel chapters mode control', () => {
  const CHAPTERS_SELECT_NAME = 'Chapters';

  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders Auto when chaptersMode is unset', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: CHAPTERS_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('auto');
  });

  it('renders the current value when chaptersMode is set', () => {
    renderPanel(makeFeed({ chaptersMode: 'generate' }));
    const select = screen.getByRole('combobox', { name: CHAPTERS_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('generate');
  });

  it('selecting Always generate fires updateFeed with chaptersMode generate', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(screen.getByRole('combobox', { name: CHAPTERS_SELECT_NAME }), 'generate');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { chaptersMode: 'generate' });
  });

  it('selecting Off fires updateFeed with chaptersMode off', async () => {
    renderPanel(makeFeed({ chaptersMode: 'generate' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: CHAPTERS_SELECT_NAME }), 'off');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { chaptersMode: 'off' });
  });

  it('selecting Auto fires updateFeed with chaptersMode auto', async () => {
    renderPanel(makeFeed({ chaptersMode: 'off' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: CHAPTERS_SELECT_NAME }), 'auto');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { chaptersMode: 'auto' });
  });
});

describe('FeedSettingsPanel source URL row (#484)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders the source URL with a copy button in read mode', () => {
    renderPanel(makeFeed());
    expect(screen.getByText('https://example.com/feed.xml')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Copy source URL' })).toBeDefined();
  });

  it('saving a changed URL calls updateFeed with sourceUrl', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const input = screen.getByPlaceholderText('https://example.com/feed.xml');
    await userEvent.clear(input);
    await userEvent.type(input, 'https://example.com/new-feed.xml');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', {
      sourceUrl: 'https://example.com/new-feed.xml',
    });
  });

  it('saving an unchanged URL exits edit mode without calling updateFeed', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mockUpdateFeed).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('saving an empty URL shows an inline error without calling updateFeed', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await userEvent.clear(screen.getByPlaceholderText('https://example.com/feed.xml'));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mockUpdateFeed).not.toHaveBeenCalled();
    expect(screen.getByText('Source URL cannot be empty')).toBeDefined();
  });

  it('a rejected save surfaces the backend message and stays in edit mode', async () => {
    mockUpdateFeed.mockRejectedValue(new Error('Could not fetch a valid RSS feed from this URL'));
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const input = screen.getByPlaceholderText('https://example.com/feed.xml');
    await userEvent.clear(input);
    await userEvent.type(input, 'https://example.com/broken.xml');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Could not fetch a valid RSS feed from this URL')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDefined();
  });
});

describe('FeedSettingsPanel segment action overrides (#565)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({
      segmentCategoryActions: { value: { cross_promo: 'beep' } },
    });
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('shows Inherit and the resolved global value for an unoverridden category', async () => {
    renderPanel(makeFeed());
    await waitFor(() => {
      const group = screen.getByRole('radiogroup', { name: 'Cross-promo action' });
      expect(within(group).getByRole('radio', { name: 'Beep' }).getAttribute('aria-checked')).toBe('true');
    });
    expect(screen.getAllByText('Inherit').length).toBeGreaterThan(0);
  });

  it('picking an action fires updateFeed with the full partial override map', async () => {
    renderPanel(makeFeed({ segmentCategoryActions: { sponsor: 'keep' } }));
    const group = await screen.findByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(within(group).getByRole('radio', { name: 'Keep' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', {
      segmentCategoryActions: { sponsor: 'keep', cross_promo: 'keep' },
    });
  });

  it('clearing the only override sends segmentCategoryActions null', async () => {
    renderPanel(makeFeed({ segmentCategoryActions: { cross_promo: 'keep' } }));
    await screen.findByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { segmentCategoryActions: null });
  });

  it('clearing one of several overrides keeps the rest', async () => {
    renderPanel(makeFeed({ segmentCategoryActions: { sponsor: 'keep', cross_promo: 'beep' } }));
    await screen.findByRole('radiogroup', { name: 'Cross-promo action' });
    const clearButtons = screen.getAllByRole('button', { name: 'Clear' });
    // Sponsor is the first category row.
    await userEvent.click(clearButtons[0]);
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', {
      segmentCategoryActions: { cross_promo: 'beep' },
    });
  });

  it('two rapid sequential edits compose into the second PATCH payload', async () => {
    // Building the payload from the `feed` prop instead of a synchronous
    // local source of truth would let a second edit read a stale prop and
    // drop the first edit (the backend replaces the override map outright,
    // it doesn't merge). The prop never changes across these two clicks.
    renderPanel(makeFeed());

    const sponsorGroup = await screen.findByRole('radiogroup', { name: 'Sponsor action' });
    await userEvent.click(within(sponsorGroup).getByRole('radio', { name: 'Keep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(1, 'test-feed', {
      segmentCategoryActions: { sponsor: 'keep' },
    });

    const crossPromoGroup = screen.getByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(within(crossPromoGroup).getByRole('radio', { name: 'Beep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(2, 'test-feed', {
      segmentCategoryActions: { sponsor: 'keep', cross_promo: 'beep' },
    });
  });

  it('a failed edit does not leak into the next edit\'s PATCH payload', async () => {
    // Without onError, a rejected PATCH would leave segmentOverrides holding
    // the failed edit; a second edit before the refetch lands would resend
    // it alongside the new one. Only the onError handler's immediate reseed
    // fixes this, since the `feed` prop never changes here to refetch it.
    mockUpdateFeed.mockRejectedValueOnce(new Error('Network error'));
    mockUpdateFeed.mockResolvedValueOnce(makeFeed());
    renderPanel(makeFeed());

    const sponsorGroup = await screen.findByRole('radiogroup', { name: 'Sponsor action' });
    await userEvent.click(within(sponsorGroup).getByRole('radio', { name: 'Keep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(1, 'test-feed', {
      segmentCategoryActions: { sponsor: 'keep' },
    });

    // Wait for the rejection to surface before firing the second edit.
    await screen.findByText('Network error');

    const crossPromoGroup = screen.getByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(within(crossPromoGroup).getByRole('radio', { name: 'Beep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(2, 'test-feed', {
      segmentCategoryActions: { cross_promo: 'beep' },
    });

    // The successful second edit clears the stale error.
    await waitFor(() => {
      expect(screen.queryByText('Network error')).toBeNull();
    });
  });

  it('a later failed edit does not erase an earlier successful edit (Lens B)', async () => {
    // Reseeding segmentOverrides from the `feed` prop on error is buggy: the
    // prop lags a just-succeeded edit until its query refetches. Restoring
    // that stale prop after B fails would erase A; the fix is rolling back
    // to the per-edit snapshot taken before B's own optimistic update.
    mockUpdateFeed.mockResolvedValueOnce(makeFeed());
    mockUpdateFeed.mockRejectedValueOnce(new Error('Network error'));
    mockUpdateFeed.mockResolvedValueOnce(makeFeed());
    renderPanel(makeFeed());

    // Edit A: sponsor -> keep. Succeeds.
    const sponsorGroup = await screen.findByRole('radiogroup', { name: 'Sponsor action' });
    await userEvent.click(within(sponsorGroup).getByRole('radio', { name: 'Keep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(1, 'test-feed', {
      segmentCategoryActions: { sponsor: 'keep' },
    });
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledTimes(1));

    // Edit B: cross_promo -> beep. Its payload correctly carries A. Fails.
    const crossPromoGroup = screen.getByRole('radiogroup', { name: 'Cross-promo action' });
    await userEvent.click(within(crossPromoGroup).getByRole('radio', { name: 'Beep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(2, 'test-feed', {
      segmentCategoryActions: { sponsor: 'keep', cross_promo: 'beep' },
    });
    await screen.findByText('Network error');

    // A must still be reflected in the UI: Sponsor still shows Keep
    // selected, with a live override (Clear button, not Inherit).
    expect(within(sponsorGroup).getByRole('radio', { name: 'Keep' }).getAttribute('aria-checked')).toBe('true');
    expect(within(sponsorGroup.parentElement as HTMLElement).getByRole('button', { name: 'Clear' })).toBeDefined();

    // B must have rolled back: Cross-promo has no override of its own.
    expect(within(crossPromoGroup.parentElement as HTMLElement).queryByRole('button', { name: 'Clear' })).toBeNull();

    // Edit C: a third edit's PATCH body must still carry A, not just C.
    const selfPromoGroup = screen.getByRole('radiogroup', { name: 'Self-promo action' });
    await userEvent.click(within(selfPromoGroup).getByRole('radio', { name: 'Beep' }));
    expect(mockUpdateFeed).toHaveBeenNthCalledWith(3, 'test-feed', {
      segmentCategoryActions: { sponsor: 'keep', self_promo: 'beep' },
    });
  });
});

describe('FeedSettingsPanel show-segments toggle (#565)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders off by default', () => {
    renderPanel(makeFeed());
    const toggle = screen.getByRole('switch', { name: 'Detect show segments' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('enabling fires updateFeed with detectShowSegments true', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('switch', { name: 'Detect show segments' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { detectShowSegments: true });
  });
});

describe('FeedSettingsPanel episode GUIDs toggle (#598)', () => {
  const TOGGLE_NAME = 'Serve MinusPod episode IDs';

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders off when ownEpisodeGuids is unset', () => {
    renderPanel(makeFeed());
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('renders on when ownEpisodeGuids is true', () => {
    renderPanel(makeFeed({ ownEpisodeGuids: true }));
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });

  it('enabling fires updateFeed with ownEpisodeGuids true', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('switch', { name: TOGGLE_NAME }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { ownEpisodeGuids: true });
  });

  it('disabling fires ownEpisodeGuids false', async () => {
    renderPanel(makeFeed({ ownEpisodeGuids: true }));
    await userEvent.click(screen.getByRole('switch', { name: TOGGLE_NAME }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { ownEpisodeGuids: false });
  });
});

describe('FeedSettingsPanel skip verification toggle (#599)', () => {
  const TOGGLE_NAME = 'Skip verification pass';

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders off when skipSecondPass is unset', () => {
    renderPanel(makeFeed());
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('renders on when skipSecondPass is true', () => {
    renderPanel(makeFeed({ skipSecondPass: true }));
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });

  it('enabling fires updateFeed with skipSecondPass true', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('switch', { name: TOGGLE_NAME }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { skipSecondPass: true });
  });

  it('disabling fires skipSecondPass false', async () => {
    renderPanel(makeFeed({ skipSecondPass: true }));
    await userEvent.click(screen.getByRole('switch', { name: TOGGLE_NAME }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { skipSecondPass: false });
  });
});

describe('FeedSettingsPanel re-render segments (#565)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
  });

  it('does nothing when the confirm dialog is dismissed', async () => {
    window.confirm = vi.fn().mockReturnValue(false);
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Re-render episodes' }));
    expect(mockRerenderSegments).not.toHaveBeenCalled();
  });

  it('confirming posts the rerender request and shows the queued/skipped result', async () => {
    window.confirm = vi.fn().mockReturnValue(true);
    mockRerenderSegments.mockResolvedValue({ queued: 3, skipped: 1 });
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Re-render episodes' }));
    expect(mockRerenderSegments).toHaveBeenCalledWith('test-feed');
    expect(await screen.findByText('3 episodes queued, 1 skipped.')).toBeDefined();
  });

  it('shows the backend error message when the request fails', async () => {
    window.confirm = vi.fn().mockReturnValue(true);
    mockRerenderSegments.mockRejectedValue(new Error('Feed not found'));
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Re-render episodes' }));
    expect(await screen.findByText('Feed not found')).toBeDefined();
  });
});
