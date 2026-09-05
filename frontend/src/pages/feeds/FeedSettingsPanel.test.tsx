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
import type { CueTemplate } from '../../api/cueTemplates';

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

const mockListCueTemplates = vi.fn().mockResolvedValue([]);

vi.mock('../../api/cueTemplates', () => ({
  listCueTemplates: (...args: unknown[]) => mockListCueTemplates(...args),
}));

const mockGetSettings = vi.fn();

vi.mock('../../api/settings', () => ({
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  getAudioSettings: () => Promise.resolve({ keepOriginalAudio: true }),
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

function makeCueTemplate(overrides: Partial<CueTemplate> = {}): CueTemplate {
  return {
    id: 1,
    podcastId: 1,
    label: 'Ad start',
    cueType: 'ad_break_start',
    sourceEpisodeId: 'ep-1',
    sourceOffsetS: 10,
    durationS: 1.5,
    sampleRate: 22050,
    nCoeffs: 13,
    scope: 'podcast',
    networkId: null,
    enabled: true,
    createdAt: '2026-01-01T00:00:00Z',
    createdBy: null,
    ...overrides,
  };
}

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

  it('renders one select with the five presets', () => {
    renderPanel(makeFeed({ processingMode: 'standard' }));
    const select = screen.getByLabelText(/processing mode/i);
    expect(select).toBeDefined();
    for (const label of ['Standard', 'Keep content only', 'Skip ad detection', 'Pass-through', 'Cue-only']) {
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

describe('FeedSettingsPanel queue priority control (#625)', () => {
  const QUEUE_PRIORITY_SELECT_NAME = 'Queue priority';

  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders Normal when queuePriority is unset', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: QUEUE_PRIORITY_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('normal');
  });

  it('renders the current value when queuePriority is low', () => {
    renderPanel(makeFeed({ queuePriority: 'low' }));
    const select = screen.getByRole('combobox', { name: QUEUE_PRIORITY_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('low');
  });

  it('selecting High fires updateFeed with queuePriority high', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(screen.getByRole('combobox', { name: QUEUE_PRIORITY_SELECT_NAME }), 'high');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { queuePriority: 'high' });
  });
});

describe('FeedSettingsPanel title blacklist controls', () => {
  const ADD_BUTTON_NAME = '+ Add pattern';
  const ACTION_SELECT_NAME = 'Skipped episodes';

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders existing patterns as chips', () => {
    renderPanel(makeFeed({ titleSkipPatterns: ['Bonus Episode *', 'Best of *'] }));
    expect(screen.getByText('Bonus Episode *')).toBeDefined();
    expect(screen.getByText('Best of *')).toBeDefined();
  });

  it('adding a pattern fires updateFeed with the appended list', async () => {
    renderPanel(makeFeed({ titleSkipPatterns: ['Bonus Episode *'] }));
    await userEvent.click(screen.getByRole('button', { name: ADD_BUTTON_NAME }));
    await userEvent.type(screen.getByLabelText('New title pattern'), 'Best of *');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', {
      titleSkipPatterns: ['Bonus Episode *', 'Best of *'],
    });
  });

  it('a failed add keeps the editor open with its value and shows the error', async () => {
    mockUpdateFeed.mockRejectedValueOnce(new Error('titleSkipPatterns entries must be strings of 1-200 characters'));
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: ADD_BUTTON_NAME }));
    const input = screen.getByLabelText('New title pattern');
    await userEvent.type(input, 'Best of *');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText('titleSkipPatterns entries must be strings of 1-200 characters')).toBeDefined();
    expect((screen.getByLabelText('New title pattern') as HTMLInputElement).value).toBe('Best of *');
  });

  it('removing a pattern fires updateFeed without it', async () => {
    renderPanel(makeFeed({ titleSkipPatterns: ['Bonus Episode *', 'Best of *'] }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove Bonus Episode *' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', {
      titleSkipPatterns: ['Best of *'],
    });
  });

  it('defaults the skip action select to serve_original', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: ACTION_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('serve_original');
  });

  it('renders the server value for the skip action select', () => {
    renderPanel(makeFeed({ titleSkipAction: 'hide' }));
    const select = screen.getByRole('combobox', { name: ACTION_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('hide');
  });

  it('selecting Hide fires updateFeed with titleSkipAction hide', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(screen.getByRole('combobox', { name: ACTION_SELECT_NAME }), 'hide');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { titleSkipAction: 'hide' });
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

describe('FeedSettingsPanel show-segments tri-state control (#565)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('selects Inherit by default and shows the effective global value', async () => {
    mockGetSettings.mockResolvedValue({ detectShowSegments: { value: false, isDefault: true } });
    renderPanel(makeFeed());

    const group = screen.getByRole('radiogroup', { name: 'Show segments' });
    await waitFor(() => {
      expect(within(group).getByRole('radio', { name: 'Inherit' }).getAttribute('aria-checked')).toBe('true');
    });
    expect(screen.getByText('Following the global setting (currently off).')).toBeDefined();
  });

  it('reflects a global default of on in the helper text', async () => {
    mockGetSettings.mockResolvedValue({ detectShowSegments: { value: true, isDefault: false } });
    renderPanel(makeFeed());

    await waitFor(() => {
      expect(screen.getByText('Following the global setting (currently on).')).toBeDefined();
    });
  });

  it('selecting On fires updateFeed with detectShowSegments true', async () => {
    renderPanel(makeFeed());
    const group = screen.getByRole('radiogroup', { name: 'Show segments' });
    await userEvent.click(within(group).getByRole('radio', { name: 'On' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { detectShowSegments: true });
  });

  it('selecting Off fires updateFeed with detectShowSegments false', async () => {
    renderPanel(makeFeed());
    const group = screen.getByRole('radiogroup', { name: 'Show segments' });
    await userEvent.click(within(group).getByRole('radio', { name: 'Off' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { detectShowSegments: false });
  });

  it('explicit true: selects On and hides the helper text', () => {
    renderPanel(makeFeed({ detectShowSegments: true }));

    const group = screen.getByRole('radiogroup', { name: 'Show segments' });
    expect(within(group).getByRole('radio', { name: 'On' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.queryByText(/Following the global setting/)).toBeNull();
  });

  it('selecting Inherit fires updateFeed with detectShowSegments null', async () => {
    renderPanel(makeFeed({ detectShowSegments: true }));

    const group = screen.getByRole('radiogroup', { name: 'Show segments' });
    await userEvent.click(within(group).getByRole('radio', { name: 'Inherit' }));

    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { detectShowSegments: null });
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

  it('is checked and disabled under cue_only, with a forced-on note', async () => {
    renderPanel(makeFeed({ processingMode: 'cue_only' }));
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Forced on by cue-only mode.')).toBeDefined();
    // Disabled: a click must not fire a PATCH.
    await userEvent.click(toggle);
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });
});

describe('FeedSettingsPanel cue-only mode controls', () => {
  const PROCESSING_SELECT_NAME = /processing mode/i;

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
    mockListCueTemplates.mockResolvedValue([]);
    // Opens the templates query gate (mirrors the panel's own storage key).
    localStorage.setItem('feed-settings-test-feed', 'true');
  });

  it('disables the cue_only option when no qualifying templates exist', async () => {
    renderPanel(makeFeed());
    await waitFor(() => expect(mockListCueTemplates).toHaveBeenCalledWith('test-feed'));
    const option = await screen.findByRole('option', { name: /cue-only/i }) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
  });

  it('enables the cue_only option once an enabled start and end template exist', async () => {
    mockListCueTemplates.mockResolvedValue([
      makeCueTemplate({ id: 1, cueType: 'ad_break_start', enabled: true }),
      makeCueTemplate({ id: 2, cueType: 'ad_break_end', enabled: true }),
    ]);
    renderPanel(makeFeed());
    const option = await screen.findByRole('option', { name: /cue-only/i }) as HTMLOptionElement;
    await waitFor(() => expect(option.disabled).toBe(false));
  });

  it('a disabled end template still leaves the option disabled', async () => {
    mockListCueTemplates.mockResolvedValue([
      makeCueTemplate({ id: 1, cueType: 'ad_break_start', enabled: true }),
      makeCueTemplate({ id: 2, cueType: 'ad_break_end', enabled: false }),
    ]);
    renderPanel(makeFeed());
    await waitFor(() => expect(mockListCueTemplates).toHaveBeenCalled());
    const option = await screen.findByRole('option', { name: /cue-only/i }) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
  });

  it('choosing cue_only sends processingMode in the PATCH', async () => {
    mockListCueTemplates.mockResolvedValue([
      makeCueTemplate({ id: 1, cueType: 'ad_break_start', enabled: true }),
      makeCueTemplate({ id: 2, cueType: 'ad_break_end', enabled: true }),
    ]);
    renderPanel(makeFeed({ processingMode: 'standard' }));
    const option = await screen.findByRole('option', { name: /cue-only/i }) as HTMLOptionElement;
    await waitFor(() => expect(option.disabled).toBe(false));
    await userEvent.selectOptions(screen.getByLabelText(PROCESSING_SELECT_NAME), 'cue_only');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { processingMode: 'cue_only' });
  });

  it('does not render the safety select or transcription toggle outside cue_only', () => {
    renderPanel(makeFeed({ processingMode: 'standard' }));
    expect(screen.queryByLabelText(/cue-only safety/i)).toBeNull();
    expect(screen.queryByRole('switch', { name: 'Skip transcription' })).toBeNull();
  });

  it('renders the safety select and transcription toggle under cue_only', () => {
    renderPanel(makeFeed({ processingMode: 'cue_only' }));
    expect(screen.getByLabelText(/cue-only safety/i)).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Skip transcription' })).toBeDefined();
  });

  it('the safety select defaults to hold_new and sends auto_cut on change', async () => {
    renderPanel(makeFeed({ processingMode: 'cue_only' }));
    const select = screen.getByLabelText(/cue-only safety/i) as HTMLSelectElement;
    expect(select.value).toBe('hold_new');
    await userEvent.selectOptions(select, 'auto_cut');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { cueOnlySafety: 'auto_cut' });
  });

  it('the transcription toggle sends skipTranscription true', async () => {
    renderPanel(makeFeed({ processingMode: 'cue_only' }));
    await userEvent.click(screen.getByRole('switch', { name: 'Skip transcription' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { skipTranscription: true });
  });
});

describe('FeedSettingsPanel experimental labelling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
    mockListCueTemplates.mockResolvedValue([]);
    localStorage.setItem('feed-settings-test-feed', 'true');
  });

  it('marks the cue_only preset experimental', async () => {
    renderPanel(makeFeed());
    const option = await screen.findByRole('option', { name: /cue-only/i });
    expect(option.textContent).toMatch(/experimental/i);
  });

  it('marks pair synthesis experimental, since it cuts on cue evidence alone', async () => {
    renderPanel(makeFeed());
    await waitFor(() => expect(screen.queryAllByText(/^Experimental$/).length).toBeGreaterThan(0));
  });

  it('keeps the badge behind the control so the row stays aligned', async () => {
    // Every input in the cue tuning section shares one left edge. The badge
    // trails the hint, so nothing sits between the label and the select.
    renderPanel(makeFeed());
    await screen.findByText('Pair synthesis:');
    const badge = screen.getByText(/^Experimental$/);
    const column = badge.parentElement!;
    const select = column.querySelector('select');
    expect(select).not.toBeNull();
    expect(column.firstElementChild).toBe(select);
    expect(select!.compareDocumentPosition(badge)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('FeedSettingsPanel re-render segments (#565)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
  });

  it('does nothing when the confirm dialog is dismissed', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Re-render episodes' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(mockRerenderSegments).not.toHaveBeenCalled();
  });

  it('confirming posts the rerender request and shows the queued/skipped result', async () => {
    mockRerenderSegments.mockResolvedValue({ queued: 3, skipped: 1 });
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Re-render episodes' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Re-render' }));
    expect(mockRerenderSegments).toHaveBeenCalledWith('test-feed');
    expect(await screen.findByText('3 episodes queued, 1 skipped.')).toBeDefined();
  });

  it('shows the backend error message when the request fails', async () => {
    mockRerenderSegments.mockRejectedValue(new Error('Feed not found'));
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Re-render episodes' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Re-render' }));
    expect(await screen.findByText('Feed not found')).toBeDefined();
  });
});

describe('FeedSettingsPanel low ad yield action override', () => {
  const LOW_YIELD_SELECT_NAME = 'Low ad yield action';

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({ lowAdYieldAction: { value: 'redetect' } });
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('falls back to the global option when the feed has no override', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: LOW_YIELD_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('names the current global action in the fallback option', async () => {
    renderPanel(makeFeed());
    await waitFor(() => {
      const select = screen.getByRole('combobox', { name: LOW_YIELD_SELECT_NAME });
      expect(within(select).getByRole('option', { name: 'Use global (Redetect ads)' })).toBeTruthy();
    });
  });

  it('renders the feed override when set', () => {
    renderPanel(makeFeed({ lowAdYieldAction: 'full' }));
    const select = screen.getByRole('combobox', { name: LOW_YIELD_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('full');
  });

  it('selecting an action fires updateFeed with that action', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: LOW_YIELD_SELECT_NAME }), 'reprocess');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { lowAdYieldAction: 'reprocess' });
  });

  it('choosing the global option clears the override', async () => {
    renderPanel(makeFeed({ lowAdYieldAction: 'full' }));
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: LOW_YIELD_SELECT_NAME }), '');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { lowAdYieldAction: null });
  });
});

describe('FeedSettingsPanel run log override', () => {
  const RUN_LOG_SELECT_NAME = 'Run log storage';

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({ episodeLogRetentionDays: { value: 30 } });
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('falls back to the global option when the feed has no override', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: RUN_LOG_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('says the global is off when retention is zero', async () => {
    mockGetSettings.mockResolvedValue({ episodeLogRetentionDays: { value: 0 } });
    renderPanel(makeFeed());
    await waitFor(() => {
      const select = screen.getByRole('combobox', { name: RUN_LOG_SELECT_NAME });
      expect(within(select).getByRole('option', { name: 'Use global (off)' })).toBeTruthy();
    });
  });

  it('renders the feed override when set', () => {
    renderPanel(makeFeed({ episodeLogs: 'off' }));
    const select = screen.getByRole('combobox', { name: RUN_LOG_SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('off');
  });

  it('selecting a value fires updateFeed with it', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: RUN_LOG_SELECT_NAME }), 'on');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { episodeLogs: 'on' });
  });

  it('choosing the global option clears the override', async () => {
    renderPanel(makeFeed({ episodeLogs: 'on' }));
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: RUN_LOG_SELECT_NAME }), '');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { episodeLogs: null });
  });
});

describe('FeedSettingsPanel splice check override', () => {
  const SELECT = 'Splice check';

  it('defaults to inheriting the global', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: SELECT }) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('renders an explicit feed override', () => {
    renderPanel(makeFeed({ spliceVetoEnabled: false }));
    const select = screen.getByRole('combobox', { name: SELECT }) as HTMLSelectElement;
    expect(select.value).toBe('false');
  });

  it('turning the check off for the feed sends false, not null', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: SELECT }), 'false');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { spliceVetoEnabled: false });
  });

  it('forcing the check on sends true', async () => {
    renderPanel(makeFeed({ spliceVetoEnabled: false }));
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: SELECT }), 'true');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { spliceVetoEnabled: true });
  });

  it('choosing the global option clears the override', async () => {
    renderPanel(makeFeed({ spliceVetoEnabled: true }));
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: SELECT }), '');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { spliceVetoEnabled: null });
  });
});

describe('FeedSettingsPanel retention overrides', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({ retentionDays: 30 });
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('shows the global window in the inherit option', async () => {
    renderPanel(makeFeed());
    expect(await screen.findByRole('option', { name: 'Use global (30 days)' })).toBeDefined();
  });

  it('archiving sends a zero override', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed());
    const select = await screen.findByLabelText('Retention');
    await user.selectOptions(select, 'archive');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ retentionDaysOverride: 0 })));
  });

  it('marks an archived feed and hides the day field', async () => {
    renderPanel(makeFeed({ retentionDaysOverride: 0 }));
    expect(await screen.findByText('Archived')).toBeDefined();
    expect(screen.queryByLabelText('Retention days')).toBeNull();
  });

  it('shows the day field for a custom window', async () => {
    renderPanel(makeFeed({ retentionDaysOverride: 7 }));
    const days = await screen.findByLabelText('Retention days') as HTMLInputElement;
    expect(days.value).toBe('7');
  });

  it('returning to global clears the override', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ retentionDaysOverride: 7 }));
    const select = await screen.findByLabelText('Retention');
    await user.selectOptions(select, 'global');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ retentionDaysOverride: null })));
  });

  it('discarding the original sends false', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed());
    const select = await screen.findByLabelText('Keep original audio');
    await user.selectOptions(select, 'off');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ keepOriginalAudioOverride: false })));
  });

  it('badges an explicit keep-original override', async () => {
    renderPanel(makeFeed({ keepOriginalAudioOverride: false }));
    expect(await screen.findByText('Override: discarding')).toBeDefined();
  });

  it('leaves the keep-original badge off while inheriting', async () => {
    renderPanel(makeFeed());
    await screen.findByLabelText('Keep original audio');
    expect(screen.queryByText(/^Override: (keeping|discarding)$/)).toBeNull();
  });
});

describe('FeedSettingsPanel retention field commit safety', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({ retentionDays: 30 });
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('does not PATCH while typing; commits once on blur', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ retentionDaysOverride: 7 }));
    const days = await screen.findByLabelText('Retention days');

    await user.clear(days);
    await user.type(days, '365');
    // Retention is the one field where an intermediate value (3, 36)
    // deletes audio if a cleanup tick lands on it.
    expect(mockUpdateFeed).not.toHaveBeenCalled();

    await user.tab();
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledTimes(1));
    expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ retentionDaysOverride: 365 }));
  });

  it('seeds Keep for with 30 days when the global window is disabled', async () => {
    const user = userEvent.setup();
    mockGetSettings.mockResolvedValue({ retentionDays: 0 });
    renderPanel(makeFeed());
    const select = await screen.findByLabelText('Retention');
    await user.selectOptions(select, 'custom');
    // Seeding from the global (0) would silently archive instead.
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ retentionDaysOverride: 30 })));
  });
});

describe('FeedSettingsPanel feed cap', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('shows the uncapped hint and a 0-10000 range on a local feed', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ feedType: 'local' }));
    await user.click(await screen.findByRole('button', { name: '+ Add network' }));

    const cap = screen.getByLabelText('Feed cap') as HTMLInputElement;
    expect(cap.min).toBe('0');
    expect(cap.max).toBe('10000');
    expect(screen.getByText('0 or empty serves every episode.')).toBeDefined();
  });

  it('sends maxEpisodes: 0 unclamped for a local feed', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ feedType: 'local' }));
    await user.click(await screen.findByRole('button', { name: '+ Add network' }));

    const cap = screen.getByLabelText('Feed cap');
    await user.clear(cap);
    await user.type(cap, '0');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ maxEpisodes: 0 })));
  });

  it('keeps the 10-500 clamp and no uncapped hint on a subscribed feed', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ feedType: 'subscribed' }));
    await user.click(await screen.findByRole('button', { name: '+ Add network' }));

    const cap = screen.getByLabelText('Feed cap') as HTMLInputElement;
    expect(cap.min).toBe('10');
    expect(cap.max).toBe('500');
    expect(screen.queryByText('0 or empty serves every episode.')).toBeNull();

    await user.clear(cap);
    await user.type(cap, '5');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ maxEpisodes: 10 })));
  });
});

describe('FeedSettingsPanel detection notes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSettings.mockResolvedValue({});
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('disables Save until the draft differs from the saved value', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ detectionNotes: 'Keep the news roundup.' }));
    const box = screen.getByLabelText('Detection notes') as HTMLTextAreaElement;
    const save = screen.getByRole('button', { name: 'Save detection notes' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await user.type(box, ' Plus this.');
    expect(save.disabled).toBe(false);
  });

  it('blur alone writes nothing', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ detectionNotes: null }));
    const box = screen.getByLabelText('Detection notes');
    await user.type(box, 'Intro has three parts.');
    await user.tab();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('Save writes detectionNotes and shows the saved confirmation', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ detectionNotes: null }));
    const box = screen.getByLabelText('Detection notes');
    await user.type(box, 'Intro has three parts.');
    await user.click(screen.getByRole('button', { name: 'Save detection notes' }));
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ detectionNotes: 'Intro has three parts.' })));
    expect(await screen.findByText('Saved')).toBeDefined();
  });

  it('Clear empties the field locally, then Save commits null', async () => {
    const user = userEvent.setup();
    renderPanel(makeFeed({ detectionNotes: 'Old note.' }));
    const box = screen.getByLabelText('Detection notes') as HTMLTextAreaElement;
    await user.click(screen.getByRole('button', { name: 'Clear detection notes' }));
    expect(box.value).toBe('');
    expect(mockUpdateFeed).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Save detection notes' }));
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith(
      'test-feed', expect.objectContaining({ detectionNotes: null })));
  });

  it('a rejected save shows an error and leaves the draft intact', async () => {
    mockUpdateFeed.mockRejectedValueOnce(new Error('Detection notes could not be saved'));
    const user = userEvent.setup();
    renderPanel(makeFeed({ detectionNotes: null }));
    const box = screen.getByLabelText('Detection notes') as HTMLTextAreaElement;
    await user.type(box, 'Intro has three parts.');
    await user.click(screen.getByRole('button', { name: 'Save detection notes' }));
    expect(await screen.findByText('Detection notes could not be saved')).toBeDefined();
    expect(box.value).toBe('Intro has three parts.');
  });
});
