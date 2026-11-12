import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import PatternDetailModal from './PatternDetailModal';
import type { AdPattern } from '../api/patterns';

const mockSplitPattern = vi.fn();
const mockUpdatePattern = vi.fn();
const mockDeletePattern = vi.fn();
const mockGetPatternAudio = vi.fn();
const mockSetPatternAudioEnabled = vi.fn();
const mockDeletePatternAudio = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  splitPattern: (...a: unknown[]) => mockSplitPattern(...a),
  updatePattern: (...a: unknown[]) => mockUpdatePattern(...a),
  deletePattern: (...a: unknown[]) => mockDeletePattern(...a),
  getPatternAudio: (...a: unknown[]) => mockGetPatternAudio(...a),
  setPatternAudioEnabled: (...a: unknown[]) => mockSetPatternAudioEnabled(...a),
  deletePatternAudio: (...a: unknown[]) => mockDeletePatternAudio(...a),
  getPatternAudioMatches: vi.fn().mockResolvedValue({ matches: [], page: 1, perPage: 10, total: 0 }),
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

const NO_AUDIO = { state: 'none', available: false, counts: { matches: 0, rejected: 0, outOfScope: 0 } };

beforeEach(() => {
  vi.clearAllMocks();
  mockGetPatternAudio.mockResolvedValue(NO_AUDIO);
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
  counts: { matches: 5, rejected: 2, outOfScope: 0 }, meanSimilarity: 0.912, lastMatchedAt: '2026-03-04T10:00:00Z',
};

describe('Audio section', () => {
  it('shows an active fingerprint with its stats and controls', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    renderModal(makePattern());
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
    renderModal(makePattern());
    expect(await screen.findByRole('heading', { name: 'Recent matches' })).toBeDefined();
    expect(await screen.findByText('No matches yet.')).toBeDefined();
  });

  it('does not list matches when the pattern has no audio', async () => {
    renderModal(makePattern());
    await screen.findByText('No audio fingerprint.');
    expect(screen.queryByRole('heading', { name: 'Recent matches' })).toBeNull();
  });

  it('shows the off state with the switch off', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, state: 'off' });
    renderModal(makePattern());
    expect(await screen.findByText('Audio off')).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Audio matching' }).getAttribute('aria-checked')).toBe('false');
  });

  it('shows the empty state for a pattern without audio', async () => {
    renderModal(makePattern());
    expect(await screen.findByText('No audio fingerprint.')).toBeDefined();
    expect(screen.queryByRole('switch', { name: 'Audio matching' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete audio' })).toBeNull();
  });

  it('disables play and says there is no source when nothing is playable', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, available: false, source: null });
    renderModal(makePattern());
    expect(await screen.findByText('No source audio')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Play source audio' })).toHaveProperty('disabled', true);
  });

  it('shows a loading status while the detail loads', () => {
    mockGetPatternAudio.mockReturnValue(new Promise(() => {}));
    renderModal(makePattern());
    expect(screen.getByRole('status', { name: 'Loading audio' })).toBeDefined();
  });

  it('shows an error with Retry when the detail fails to load', async () => {
    mockGetPatternAudio.mockRejectedValueOnce(new Error('boom'));
    renderModal(makePattern());
    expect(await screen.findByText('Could not load audio.')).toBeDefined();
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('91%')).toBeDefined();
  });

  it('notes matches found outside the pattern scope', async () => {
    mockGetPatternAudio.mockResolvedValue({ ...ACTIVE_AUDIO, counts: { matches: 5, rejected: 0, outOfScope: 3 } });
    renderModal(makePattern());
    expect(await screen.findByText("3 matches came from episodes outside this pattern's scope.")).toBeDefined();
  });

  it('turns audio off at once and announces it', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockSetPatternAudioEnabled.mockResolvedValue({ ...ACTIVE_AUDIO, state: 'off' });
    const { invalidateSpy } = renderModal(makePattern());
    await userEvent.click(await screen.findByRole('switch', { name: 'Audio matching' }));
    expect(mockSetPatternAudioEnabled).toHaveBeenCalledWith(42, false);
    expect((await screen.findByRole('status', { name: 'Audio change' })).textContent).toBe('Audio matching turned off.');
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
  });

  it('reverts the switch and shows the error when the change fails', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    mockSetPatternAudioEnabled.mockRejectedValue(new Error('boom'));
    renderModal(makePattern());
    await userEvent.click(await screen.findByRole('switch', { name: 'Audio matching' }));
    expect(await screen.findByText('Could not turn audio off.')).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Audio matching' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Audio on')).toBeDefined();
  });

  it('confirms before deleting audio, then shows the empty state', async () => {
    mockGetPatternAudio.mockResolvedValueOnce(ACTIVE_AUDIO);
    mockDeletePatternAudio.mockResolvedValue(undefined);
    const { invalidateSpy } = renderModal(makePattern());
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
    renderModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Delete audio' }));
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Delete audio?' })).getByRole('button', { name: 'Delete audio' }));
    expect(await screen.findByText('Could not delete audio.')).toBeDefined();
    expect(screen.getByText('91%')).toBeDefined();
  });

  it('says why the source clip cannot play when the server is busy', async () => {
    mockGetPatternAudio.mockResolvedValue(ACTIVE_AUDIO);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('no'));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) } as unknown as Response));
    renderModal(makePattern());
    await userEvent.click(await screen.findByRole('button', { name: 'Play source audio' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Audio is being prepared. Try again.');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
});
