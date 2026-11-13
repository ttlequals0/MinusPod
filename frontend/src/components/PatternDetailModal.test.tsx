import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import PatternDetailModal from './PatternDetailModal';
import { ApiError } from '../api/client';
import type { AdPattern } from '../api/patterns';
import { formatDateTimeParts } from '../utils/format';

const mockSplitPattern = vi.fn();
const mockUpdatePattern = vi.fn();
const mockDeletePattern = vi.fn();
const mockGetPatternAudio = vi.fn();
const mockSetPatternAudioEnabled = vi.fn();
const mockDeletePatternAudio = vi.fn();
const mockRebuildPatternAudio = vi.fn();
const mockGetEpisode = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  splitPattern: (...a: unknown[]) => mockSplitPattern(...a),
  updatePattern: (...a: unknown[]) => mockUpdatePattern(...a),
  deletePattern: (...a: unknown[]) => mockDeletePattern(...a),
  getPatternAudio: (...a: unknown[]) => mockGetPatternAudio(...a),
  setPatternAudioEnabled: (...a: unknown[]) => mockSetPatternAudioEnabled(...a),
  deletePatternAudio: (...a: unknown[]) => mockDeletePatternAudio(...a),
  rebuildPatternAudio: (...a: unknown[]) => mockRebuildPatternAudio(...a),
  getPatternAudioMatches: vi.fn().mockResolvedValue({ matches: [], page: 1, perPage: 10, total: 0 }),
}));

vi.mock('../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/feeds')>()),
  getEpisode: (...a: unknown[]) => mockGetEpisode(...a),
}));

// The editor itself is covered by AdReviewModal.test.tsx; this double only drives the save path.
vi.mock('./AdReviewModal', () => ({
  default: function AdReviewModalDouble({ item, mode, episodeDuration, onSaveSpan, onClose }: {
    item: { podcastSlug: string; episodeId: string; start: number; end: number };
    mode: string;
    episodeDuration: number;
    onSaveSpan: (s: { start: number; end: number }) => Promise<void>;
    onClose: () => void;
  }) {
    const [error, setError] = useState('');
    return (
      <div role="dialog" aria-label="Edit edges" data-mode={mode} data-duration={episodeDuration}
        data-span={`${item.podcastSlug}/${item.episodeId}/${item.start}-${item.end}`}>
        <button type="button" onClick={() => { onSaveSpan({ start: 12, end: 48 }).catch((e: Error) => setError(e.message)); }}>Save audio</button>
        <button type="button" onClick={onClose}>Cancel</button>
        {error && <p role="alert">{error}</p>}
      </div>
    );
  },
}));

vi.mock('../api/sponsors', () => ({
  getSponsors: vi.fn().mockResolvedValue([]),
  addSponsor: vi.fn(),
}));

function makePattern(overrides: Partial<AdPattern> = {}): AdPattern {
  return {
    id: 42,
    scope: 'podcast',
    network_id: null,
    podcast_id: 'show-a',
    dai_platform: null,
    text_template: 'This episode is brought to you by Acme.',
    intro_variants: '[]',
    outro_variants: '[]',
    sponsor: 'Acme',
    confirmation_count: 3,
    false_positive_count: 0,
    last_matched_at: null,
    created_at: '2026-01-01T00:00:00Z',
    created_from_episode_id: null,
    is_active: true,
    can_split: true,
    disabled_at: null,
    disabled_reason: null,
    ...overrides,
  };
}

function renderModal(pattern: AdPattern, onClose = vi.fn(), onSave = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <PatternDetailModal pattern={pattern} onClose={onClose} onSave={onSave} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { invalidateSpy };
}

// The audio controls live on the Audio tab.
function renderAudioModal(...args: Parameters<typeof renderModal>) {
  const result = renderModal(...args);
  fireEvent.click(screen.getByRole('tab', { name: 'Audio' }));
  return result;
}

const NO_AUDIO = { state: 'none', available: false, counts: { matches: 0, rejected: 0, outOfScope: 0 } };

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.clearAllMocks();
  mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
  mockGetEpisode.mockResolvedValue({ id: 'a1b2c3d4e5f6', originalDuration: 1800, hasOriginalAudio: true, adMarkers: [] });
});

describe('Split button', () => {
  it('renders for an active pattern', () => {
    renderModal(makePattern({ is_active: true }));
    expect(screen.getByRole('button', { name: 'Split' })).toBeDefined();
  });

  it('does not render for an inactive pattern', () => {
    renderModal(makePattern({ is_active: false }));
    expect(screen.queryByRole('button', { name: 'Split' })).toBeNull();
  });

  it('explains why an active pattern cannot be split', async () => {
    renderModal(makePattern({ can_split: false }));
    const button = screen.getByRole('button', { name: 'Split' });
    expect(button).toHaveProperty('disabled', true);
    expect(screen.getByText('No reliable ad boundary found.')).toBeDefined();
    await userEvent.click(button);
    expect(mockSplitPattern).not.toHaveBeenCalled();
  });

  it('splits successfully, invalidates the patterns query, and closes', async () => {
    mockSplitPattern.mockResolvedValue({
      success: true, original_pattern_id: 42, new_pattern_ids: [43, 44],
      message: 'Split into 2 patterns',
    });
    const onClose = vi.fn();
    const { invalidateSpy } = renderModal(makePattern(), onClose);

    await userEvent.click(screen.getByRole('button', { name: 'Split' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockSplitPattern).toHaveBeenCalledWith(42);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
  });

  it('disables the button while the mutation is pending', async () => {
    let resolveSplit: (v: unknown) => void = () => {};
    mockSplitPattern.mockReturnValue(new Promise((resolve) => { resolveSplit = resolve; }));
    renderModal(makePattern());

    const button = screen.getByRole('button', { name: 'Split' });
    await userEvent.click(button);

    await waitFor(() => expect(
      screen.getByRole('button', { name: 'Splitting...' }),
    ).toHaveProperty('disabled', true));
    resolveSplit({ success: true, original_pattern_id: 42, new_pattern_ids: [43], message: 'ok' });
  });

  it('shows the API error text on a 400 (no split points) without crashing', async () => {
    mockSplitPattern.mockRejectedValue(new Error('Pattern 42 not found or has nothing to split'));
    renderModal(makePattern());

    await userEvent.click(screen.getByRole('button', { name: 'Split' }));

    await waitFor(() => expect(
      screen.getByText('Pattern 42 not found or has nothing to split'),
    ).toBeDefined());
    // Component stays mounted and interactive after the error.
    expect(screen.getByRole('button', { name: 'Split' })).toBeDefined();
  });
});

describe('Category editing', () => {
  it('shows a category select preloaded with the stored category', async () => {
    renderModal(makePattern({ category: 'cross_promo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));

    const select = screen.getByLabelText('Category') as HTMLSelectElement;
    expect(select.value).toBe('cross_promo');
  });

  it('saves a changed category through updatePattern', async () => {
    mockUpdatePattern.mockResolvedValue(undefined);
    renderModal(makePattern({ category: 'cross_promo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));

    await userEvent.selectOptions(screen.getByLabelText('Category'), 'sponsor');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdatePattern).toHaveBeenCalledWith(
      42, expect.objectContaining({ category: 'sponsor' }),
    ));
  });

  it('clearing the category saves null so the backend unsets it', async () => {
    mockUpdatePattern.mockResolvedValue(undefined);
    renderModal(makePattern({ category: 'cross_promo' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));

    await userEvent.selectOptions(screen.getByLabelText('Category'), '');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdatePattern).toHaveBeenCalledWith(
      42, expect.objectContaining({ category: null }),
    ));
  });
});

describe('Footer actions', () => {
  it('shows Split, Delete, Close and Edit as text buttons at every width', () => {
    renderModal(makePattern());
    for (const name of ['Split', 'Delete', 'Close', 'Edit']) {
      const button = screen.getByRole('button', { name });
      expect(button.textContent).toBe(name);
      expect(button.querySelector('svg')).toBeNull();
    }
  });

  it('confirms delete through the Delete pattern and Cancel buttons', async () => {
    mockDeletePattern.mockResolvedValue(undefined);
    const onClose = vi.fn();
    renderModal(makePattern(), onClose);
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Delete pattern' }));
    await waitFor(() => expect(mockDeletePattern).toHaveBeenCalledWith(42));
  });
});

describe('podcast link', () => {
  it('links to the feed page, not the API episodes path', () => {
    renderModal(makePattern({ podcast_slug: 'example-podcast' }));
    expect(screen.getByRole('link', { name: 'example-podcast' }).getAttribute('href')).toBe('/feeds/example-podcast');
  });
});

const ACTIVE_AUDIO = {
  state: 'active', available: true, length: 45, createdAt: '2026-01-02T00:00:00Z',
  builtFrom: { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 10, end: 55, available: true },
  source: { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 10, end: 55 },
  editSource: { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 10, end: 55 },
  counts: { matches: 5, rejected: 2, outOfScope: 0 }, meanSimilarity: 0.912, lastMatchedAt: '2026-03-04T10:00:00Z',
};

describe('Audio section', () => {
  it('says the match history is still being filled in only while the backfill runs', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, historyBackfillPending: true });
    renderAudioModal(makePattern());
    expect(await screen.findByText('Match history is still being filled in.')).toBeDefined();
  });

  it('shows no backfill line once the history is complete', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, historyBackfillPending: false });
    renderAudioModal(makePattern());
    expect(await screen.findByText('91%')).toBeDefined();
    expect(screen.queryByText('Match history is still being filled in.')).toBeNull();
  });

  it('shows an active fingerprint with its stats and controls', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderAudioModal(makePattern());
    expect(await screen.findByText('91%')).toBeDefined();
    expect(mockGetPatternAudio).toHaveBeenCalledWith(42);
    expect(screen.getByRole('heading', { name: 'Audio' })).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Audio matching' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Audio on')).toBeDefined();
    expect(screen.getByText('5')).toBeDefined();
    expect(screen.getByText('2')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Delete audio' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Play source audio' })).toHaveProperty('disabled', false);
  });

  it('lists recent matches under an active fingerprint', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderAudioModal(makePattern());
    expect(await screen.findByRole('heading', { name: 'Recent matches' })).toBeDefined();
    expect(await screen.findByText('No matches yet.')).toBeDefined();
  });

  it('does not list matches when the pattern has no audio', async () => {
    renderAudioModal(makePattern());
    await screen.findByText('No audio fingerprint.');
    expect(screen.queryByRole('heading', { name: 'Recent matches' })).toBeNull();
  });

  it('shows the off state with the switch off', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, state: 'off' });
    renderAudioModal(makePattern());
    expect(await screen.findByText('Audio off')).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Audio matching' }).getAttribute('aria-checked')).toBe('false');
  });

  it('shows the empty state for a pattern without audio', async () => {
    renderAudioModal(makePattern());
    expect(await screen.findByText('No audio fingerprint.')).toBeDefined();
    expect(screen.queryByRole('switch', { name: 'Audio matching' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete audio' })).toBeNull();
  });

  it('disables play and says there is no source when nothing is playable', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, available: false, source: null });
    renderAudioModal(makePattern());
    expect(await screen.findByText('No source audio')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Play source audio' })).toHaveProperty('disabled', true);
  });

  it('shows a loading status while the detail loads', () => {
    mockGetPatternAudio.mockReturnValue(new Promise(() => {}));
    renderAudioModal(makePattern());
    expect(screen.getByRole('status', { name: 'Loading audio' })).toBeDefined();
  });

  it('shows an error with Retry when the detail fails to load', async () => {
    mockGetPatternAudio.mockRejectedValueOnce(new Error('boom'));
    renderAudioModal(makePattern());
    expect(await screen.findByText('Could not load audio.')).toBeDefined();
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('91%')).toBeDefined();
  });

  it('notes matches found outside the pattern scope', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, counts: { matches: 5, rejected: 0, outOfScope: 3 } });
    renderAudioModal(makePattern());
    expect(await screen.findByText("3 matches came from episodes outside this pattern's scope.")).toBeDefined();
  });

  it('turns audio off at once and announces it', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockSetPatternAudioEnabled.mockResolvedValue({ ...ACTIVE_AUDIO, state: 'off' });
    const { invalidateSpy } = renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('switch', { name: 'Audio matching' }));
    expect(mockSetPatternAudioEnabled).toHaveBeenCalledWith(42, false);
    expect((await screen.findByRole('status', { name: 'Audio change' })).textContent).toBe('Audio matching turned off.');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
  });

  it('reverts the switch and shows the error when the change fails', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockSetPatternAudioEnabled.mockRejectedValue(new Error('boom'));
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('switch', { name: 'Audio matching' }));
    expect(await screen.findByText('Could not turn audio off.')).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Audio matching' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Audio on')).toBeDefined();
  });

  it('confirms before deleting audio, then shows the empty state', async () => {
    mockGetPatternAudio.mockResolvedValueOnce(ACTIVE_AUDIO);
    mockDeletePatternAudio.mockResolvedValue(undefined);
    const { invalidateSpy } = renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Delete audio' }));
    expect(mockDeletePatternAudio).not.toHaveBeenCalled();
    const dialog = screen.getByRole('dialog', { name: 'Delete audio?' });
    mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete audio' }));
    await waitFor(() => expect(mockDeletePatternAudio).toHaveBeenCalledWith(42));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
    expect(await screen.findByText('No audio fingerprint.')).toBeDefined();
  });

  it('keeps the audio and shows the error when delete fails', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockDeletePatternAudio.mockRejectedValue(new Error('boom'));
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Delete audio' }));
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Delete audio?' })).getByRole('button', { name: 'Delete audio' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete audio?' });
    expect(await within(dialog).findByText('Could not delete audio. Try again.')).toBeDefined();
    mockDeletePatternAudio.mockResolvedValue(undefined);
    mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete audio' }));
    await waitFor(() => expect(mockDeletePatternAudio).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Delete audio?' })).toBeNull());
  });

  it('says why the source clip cannot play when the server is busy', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 503, headers: { 'Retry-After': '2' } })));
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Play source audio' }));
    fireEvent.error(document.querySelector('audio') as HTMLAudioElement);
    expect((await screen.findByRole('alert')).textContent).toBe('Audio is being prepared. Try again.');
    await userEvent.click(screen.getByRole('button', { name: 'Play source audio' }));
    fireEvent.playing(document.querySelector('audio') as HTMLAudioElement);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause source audio' })).toBeDefined());
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says that the switch applies immediately', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderAudioModal(makePattern());
    expect(await screen.findByText('Applies immediately.')).toBeDefined();
  });

  it('moves focus to the Audio heading once the audio is deleted', async () => {
    mockGetPatternAudio.mockResolvedValueOnce(ACTIVE_AUDIO);
    mockDeletePatternAudio.mockResolvedValue(undefined);
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Delete audio' }));
    mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Delete audio?' })).getByRole('button', { name: 'Delete audio' }));
    await screen.findByText('No audio fingerprint.');
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Audio' })));
  });

  it('refetches the detail when a switch change fails, so a vanished fingerprint shows', async () => {
    mockGetPatternAudio.mockResolvedValueOnce(ACTIVE_AUDIO);
    mockSetPatternAudioEnabled.mockRejectedValue(new ApiError('none', 404, { code: 'no_audio' }));
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('switch', { name: 'Audio matching' }));
    mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
    expect(await screen.findByText('No audio fingerprint.')).toBeDefined();
  });

  it('refetches the detail when a delete fails', async () => {
    mockGetPatternAudio.mockResolvedValueOnce(ACTIVE_AUDIO);
    mockDeletePatternAudio.mockRejectedValue(new ApiError('none', 404, { code: 'no_audio' }));
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Delete audio' }));
    mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Delete audio?' })).getByRole('button', { name: 'Delete audio' }));
    expect(await screen.findByText('No audio fingerprint.')).toBeDefined();
  });

  it('lays the four tiles out two by two below sm and in one row from sm, with date over time at every width', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderAudioModal(makePattern());
    const tile = (await screen.findByText('Mean match')).closest('[class*="grid"]')?.parentElement as HTMLElement;
    expect(tile.className).toContain('grid-cols-2');
    expect(tile.className).toContain('sm:grid-cols-4');
    expect(tile.className).not.toContain('auto-rows-fr');
    const parts = formatDateTimeParts(ACTIVE_AUDIO.lastMatchedAt);
    const dateLine = screen.getByText(parts.date);
    const timeLine = screen.getByText(parts.time);
    expect(dateLine.parentElement).toBe(timeLine.parentElement);
    expect(tile.querySelector('.sm\\:hidden, .max-sm\\:hidden')).toBeNull();
  });
});

describe('Add audio to pattern from the source episode', () => {
  const SOURCE = { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 300, end: 345, available: true };
  const noAudio = (addSource: unknown) => ({ ...NO_AUDIO, addSource });

  it('adds audio from the span the server names and announces it', async () => {
    mockGetPatternAudio.mockResolvedValue(noAudio(SOURCE));
    mockRebuildPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, fingerprintStored: true });
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    await waitFor(() => expect(mockRebuildPatternAudio).toHaveBeenCalledWith(
      42, { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 300, end: 345 },
    ));
    expect(mockGetEpisode).not.toHaveBeenCalled();
    expect((await screen.findByRole('status', { name: 'Audio change' })).textContent).toBe('Audio added to pattern.');
  });

  it('moves focus to the Audio heading once the audio is added', async () => {
    mockGetPatternAudio.mockResolvedValueOnce(noAudio(SOURCE)).mockResolvedValue(ACTIVE_AUDIO);
    mockRebuildPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, fingerprintStored: true });
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    await waitFor(() => expect(document.activeElement?.textContent).toBe('Audio'));
  });

  it('shows the reason when no fingerprint was stored', async () => {
    mockGetPatternAudio.mockResolvedValue(noAudio(SOURCE));
    mockRebuildPatternAudio.mockResolvedValue({ ...NO_AUDIO, fingerprintStored: false, fingerprintSkippedReason: 'segment_too_short' });
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    expect((await screen.findByRole('alert')).textContent).toBe('That selection is too short for an audio fingerprint.');
  });

  it('is disabled with a visible reason when the source original is gone', async () => {
    mockGetPatternAudio.mockResolvedValue(noAudio({ ...SOURCE, available: false }));
    renderAudioModal(makePattern());
    const button = await screen.findByRole('button', { name: 'Add audio to pattern' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-describedby')).toBe(screen.getByText('Source audio expired').id);
  });

  it('stays visible but disabled, described by the reason, when the server knows no source', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...NO_AUDIO, addSource: null });
    renderAudioModal(makePattern());
    const button = await screen.findByRole('button', { name: 'Add audio to pattern' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-describedby')).toBe(screen.getByText(/No source audio is available/).id);
    await userEvent.click(button);
    expect(mockGetEpisode).not.toHaveBeenCalled();
  });
});

describe('Dialog layout classes', () => {
  it('top-aligns the footer groups so Close and Edit keep their own height beside the Split reason', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderModal(makePattern({ can_split: false }));
    const close = await screen.findByRole('button', { name: 'Close' });
    expect(close.parentElement?.className).toContain('items-center');
    expect(close.parentElement?.parentElement?.className).toContain('items-start');
  });

  it('keeps the Split button at its own width beside the reason line', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderModal(makePattern({ can_split: false }));
    const split = await screen.findByRole('button', { name: 'Split' });
    expect(split.parentElement?.className).toContain('items-start');
    expect(split.parentElement?.className).toContain('flex-col');
  });

  it('keeps the stat tiles at least 7rem wide so a label does not touch the tile edge', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderModal(makePattern());
    const tile = (await screen.findByText('Confirmations')).closest('[class*="grid"]')?.parentElement as HTMLElement;
    expect(tile.className).toContain('minmax(7rem,1fr)');
  });
});

describe('Edit edges', () => {
  const EXPIRED = { ...ACTIVE_AUDIO, available: false, source: null, editSource: null, builtFrom: { ...ACTIVE_AUDIO.builtFrom, available: false } };

  it('edits the latest retained match after the original source expires', async () => {
    mockGetPatternAudio.mockResolvedValue({
      ...ACTIVE_AUDIO,
      builtFrom: { ...ACTIVE_AUDIO.builtFrom, available: false },
      editSource: { podcastSlug: 'example-podcast', episodeId: 'b2c3d4e5f6a1', start: 20, end: 65 },
    });
    mockRebuildPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, fingerprintStored: true });
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Edit edges' }));
    const editor = await screen.findByRole('dialog', { name: 'Edit edges' });
    expect(editor.dataset.span).toBe('example-podcast/b2c3d4e5f6a1/20-65');
    await userEvent.click(within(editor).getByRole('button', { name: 'Save audio' }));
    await waitFor(() => expect(mockRebuildPatternAudio).toHaveBeenCalledWith(
      42, { podcastSlug: 'example-podcast', episodeId: 'b2c3d4e5f6a1', start: 12, end: 48 },
    ));
  });

  it('opens the editor on the source episode with the built span and saves through the rebuild route', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockRebuildPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, fingerprintStored: true });
    const { invalidateSpy } = renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    await userEvent.click(opener);
    const editor = await screen.findByRole('dialog', { name: 'Edit edges' });
    expect(editor.dataset.mode).toBe('span');
    expect(editor.dataset.span).toBe('example-podcast/a1b2c3d4e5f6/10-55');
    expect(editor.dataset.duration).toBe('1800');
    expect(mockGetEpisode).toHaveBeenCalledWith('example-podcast', 'a1b2c3d4e5f6');
    mockGetPatternAudio.mockClear();
    await userEvent.click(within(editor).getByRole('button', { name: 'Save audio' }));
    await waitFor(() => expect(mockRebuildPatternAudio).toHaveBeenCalledWith(
      42, { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 12, end: 48 },
    ));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Edit edges' })).toBeNull());
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pattern-audio', 42] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pattern-audio-matches', 42] });
    await waitFor(() => expect(mockGetPatternAudio).toHaveBeenCalled());
  });

  it('loads the source episode only when Edit edges is clicked', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    expect(mockGetEpisode).not.toHaveBeenCalled();
    await userEvent.click(opener);
    await screen.findByRole('dialog', { name: 'Edit edges' });
    expect(mockGetEpisode).toHaveBeenCalledTimes(1);
  });

  it('stops a playing preview before the editor opens', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    renderAudioModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Play source audio' }));
    fireEvent.playing(document.querySelector('audio') as HTMLAudioElement);
    expect(screen.getByRole('button', { name: 'Pause source audio' })).toBeDefined();
    const opener = screen.getByRole('button', { name: 'Edit edges' });
    await userEvent.click(opener);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Play source audio' })).toBeDefined();
  });

  it('bounds the editor at the span end plus 30 s when the episode has no duration', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockGetEpisode.mockResolvedValue({ id: 'a1b2c3d4e5f6', hasOriginalAudio: true, adMarkers: [] });
    renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    await userEvent.click(opener);
    expect((await screen.findByRole('dialog', { name: 'Edit edges' })).dataset.duration).toBe('85');
  });

  it('returns focus to Edit edges when the editor closes', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    await userEvent.click(opener);
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Edit edges' })).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Edit edges' })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Edit edges' }));
  });

  it('shows the reason and keeps the editor open when no fingerprint was stored', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockRebuildPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, fingerprintStored: false, fingerprintSkippedReason: 'not_distinctive' });
    const { invalidateSpy } = renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    await userEvent.click(opener);
    invalidateSpy.mockClear();
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Edit edges' })).getByRole('button', { name: 'Save audio' }));
    expect((await screen.findByRole('alert')).textContent).toBe('That audio is not distinctive enough to match reliably.');
    expect(screen.getByRole('dialog', { name: 'Edit edges' })).toBeDefined();
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it('shows the server message for a span it refuses', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockRebuildPatternAudio.mockRejectedValue(new Error('span must be at most 600 seconds'));
    renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    await userEvent.click(opener);
    await userEvent.click(within(await screen.findByRole('dialog', { name: 'Edit edges' })).getByRole('button', { name: 'Save audio' }));
    expect((await screen.findByRole('alert')).textContent).toBe('span must be at most 600 seconds');
  });

  it('disables the action and says the source audio expired', async () => {
    mockGetPatternAudio.mockResolvedValue(EXPIRED);
    renderAudioModal(makePattern());
    const opener = await screen.findByRole('button', { name: 'Edit edges' });
    expect((opener as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Source audio expired')).toBeDefined();
    expect(mockGetEpisode).not.toHaveBeenCalled();
  });

  it('is not offered without an audio fingerprint', async () => {
    renderAudioModal(makePattern());
    await screen.findByText('No audio fingerprint.');
    expect(screen.queryByRole('button', { name: 'Edit edges' })).toBeNull();
  });
});

describe('Dialog tabs', () => {
  it('shows Details first and moves audio into its own tab with tab semantics', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderModal(makePattern());
    const details = screen.getByRole('tab', { name: 'Details' });
    const audio = screen.getByRole('tab', { name: 'Audio' });
    expect(details.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(details.id);
    const panelOf = (name: string) => screen.getByText(name).closest('[role="tabpanel"]') as HTMLElement;
    expect(panelOf('Confirmations').hidden).toBe(false);
    expect(details.getAttribute('aria-controls')).toBe('pattern-dialog-panel-details');
    expect(document.getElementById(audio.getAttribute('aria-controls')!)?.hidden).toBe(true);
    await userEvent.click(audio);
    expect(await screen.findByText('Audio matching')).toBeDefined();
    expect(panelOf('Audio matching').hidden).toBe(false);
    expect(panelOf('Confirmations').hidden).toBe(true);
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(audio.id);
  });

  it('stops a playing preview when the Audio tab is left, and keeps the section mounted', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    renderModal(makePattern());
    await userEvent.click(screen.getByRole('tab', { name: 'Audio' }));
    await screen.findByText('Audio matching');
    pause.mockClear();
    await userEvent.click(screen.getByRole('tab', { name: 'Details' }));
    expect(pause).toHaveBeenCalled();
    expect(document.getElementById('pattern-dialog-panel-audio')?.textContent).toContain('Audio matching');
  });

  it('moves between tabs with the arrow, Home and End keys', async () => {
    renderModal(makePattern());
    const details = screen.getByRole('tab', { name: 'Details' });
    details.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Audio' }));
    await userEvent.keyboard('{Home}');
    expect(document.activeElement).toBe(details);
    await userEvent.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Audio' }).getAttribute('aria-selected')).toBe('true');
  });

  it('keeps the draft while the Audio tab is open', async () => {
    renderModal(makePattern({ sponsor: 'Acme' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const sponsor = screen.getByLabelText('Sponsor');
    await userEvent.clear(sponsor);
    await userEvent.type(sponsor, 'Beta');
    await userEvent.click(screen.getByRole('tab', { name: 'Audio' }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDefined();
    await userEvent.click(screen.getByRole('tab', { name: 'Details' }));
    expect((screen.getByLabelText('Sponsor') as HTMLInputElement).value).toBe('Beta');
  });

  it('opens Details when Edit is chosen from the Audio tab', async () => {
    renderModal(makePattern());
    await userEvent.click(screen.getByRole('tab', { name: 'Audio' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('tab', { name: 'Details' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByLabelText('Sponsor')).toBeDefined();
  });
});

describe('Audio tab reasons', () => {
  it('names the other route when no episode can source the audio', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...NO_AUDIO, addSource: null });
    renderAudioModal(makePattern());
    expect(await screen.findByText(/Use Add audio to pattern on a text pattern detection in the Detected ads tab or on the episode page/)).toBeDefined();
  });

  it('does not give that reason when a source is offered', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...NO_AUDIO, addSource: { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 1, end: 9, available: true } });
    renderAudioModal(makePattern());
    await screen.findByRole('button', { name: 'Add audio to pattern' });
    expect(screen.queryByText(/text pattern detection/)).toBeNull();
  });

  it('says why Edit edges is missing when the fingerprint has no source', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, builtFrom: null, source: null, editSource: null, available: false });
    renderAudioModal(makePattern());
    expect(await screen.findByText('Edit edges needs source audio.')).toBeDefined();
    expect((screen.getByRole('button', { name: 'Edit edges' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('offers Edit edges for a legacy fingerprint whose source the server resolved', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, builtFrom: { ...ACTIVE_AUDIO.builtFrom, start: 20, end: 50 } });
    renderAudioModal(makePattern());
    expect(await screen.findByRole('button', { name: 'Edit edges' })).toBeDefined();
    expect(screen.queryByText('Edit edges needs source audio.')).toBeNull();
  });
});
