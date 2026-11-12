import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AudioMatchesList from './AudioMatchesList';
import { usePatternAudioPreview } from '../hooks/usePatternAudioPreview';
import type { AudioMatch } from '../api/patterns';

const mockGetMatches = vi.fn();
const mockSubmitCorrection = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  getPatternAudioMatches: (...a: unknown[]) => mockGetMatches(...a),
  submitCorrection: (...a: unknown[]) => mockSubmitCorrection(...a),
}));

function makeMatch(i: number, overrides: Partial<AudioMatch> = {}): AudioMatch {
  return {
    id: 100 + i, episodeId: `ep${i}`, podcastSlug: 'example-podcast', episodeTitle: `Episode ${i}`,
    start: 300 + i, end: 338 + i, similarity: 0.912, coverage: 0.85, storedLength: 45, inScope: true,
    audioOnly: false, rejected: false, available: true, createdAt: '2026-03-04T10:00:00Z', ...overrides,
  };
}

function pageOf(all: AudioMatch[], page: number) {
  return { matches: all.slice((page - 1) * 10, page * 10), page, perPage: 10, total: all.length };
}

function Harness() {
  const preview = usePatternAudioPreview(7);
  return <><AudioMatchesList patternId={7} preview={preview} />{preview.audioElement}</>;
}

function renderList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
  render(<QueryClientProvider client={qc}><Harness /></QueryClientProvider>);
  return { invalidateSpy };
}

const twelve = Array.from({ length: 12 }, (_, i) => makeMatch(i + 1));

beforeEach(() => {
  vi.clearAllMocks();
  mockGetMatches.mockImplementation((_id: number, p: { page?: number } = {}) => Promise.resolve(pageOf(twelve, p.page ?? 1)));
});
afterEach(() => vi.restoreAllMocks());

describe('AudioMatchesList', () => {
  it('shows ten rows on page 1 and the rest on page 2', async () => {
    renderList();
    expect(await screen.findAllByRole('listitem')).toHaveLength(10);
    expect(mockGetMatches).toHaveBeenCalledWith(7, { page: 1, perPage: 10 });
    await userEvent.click(screen.getAllByRole('button', { name: 'Page 2' })[0]);
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2));
    expect(mockGetMatches).toHaveBeenCalledWith(7, { page: 2, perPage: 10 });
    expect(screen.getByText('Episode 11')).toBeDefined();
  });

  it('shows position, match and how much of the stored ad was covered', async () => {
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    expect(within(row).getByText('5:01')).toBeDefined();
    expect(within(row).getByText('91% match')).toBeDefined();
    expect(within(row).getByText('38s of 45s')).toBeDefined();
    expect(within(row).getByText('Matched')).toBeDefined();
  });

  it('labels audio-only finds and out-of-scope matches', async () => {
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1, { audioOnly: true }), makeMatch(2, { inScope: false })], 1));
    renderList();
    const rows = await screen.findAllByRole('listitem');
    expect(within(rows[0]).getByText('Found by audio only')).toBeDefined();
    expect(within(rows[1]).getByText('Out of scope')).toBeDefined();
  });

  it('says the source expired and offers no actions on a row without its original', async () => {
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1, { available: false })], 1));
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    expect(within(row).getByText('Source audio expired')).toBeDefined();
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('keeps a rejected row in the list, marked, with no Not this ad action', async () => {
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1, { rejected: true })], 1));
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    expect(within(row).getByText('Not this ad')).toBeDefined();
    expect(within(row).queryByRole('button', { name: 'Not this ad' })).toBeNull();
    expect(within(row).getByRole('button', { name: /^Play/ })).toBeDefined();
  });

  it('sends a reject correction with the measured span and marks the row', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    const { invalidateSpy } = renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    mockGetMatches.mockImplementation((_id: number, p: { page?: number } = {}) => Promise.resolve(
      pageOf(twelve.map((m) => (m.id === 101 ? { ...m, rejected: true } : m)), p.page ?? 1)));
    await userEvent.click(within(row).getByRole('button', { name: 'Not this ad' }));
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledWith('example-podcast', 'ep1', {
      type: 'reject', original_ad: { start: 301, end: 339, pattern_id: 7 },
    }));
    await waitFor(() => expect(within(screen.getAllByRole('listitem')[0]).queryByRole('button', { name: 'Not this ad' })).toBeNull());
    expect(within(screen.getAllByRole('listitem')[0]).getByText('Not this ad')).toBeDefined();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pattern-audio', 7] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pattern-audio-matches', 7] });
  });

  it('leaves the row alone and shows an error when the correction fails', async () => {
    mockSubmitCorrection.mockRejectedValue(new Error('boom'));
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    await userEvent.click(within(row).getByRole('button', { name: 'Not this ad' }));
    expect(await screen.findByText('Could not save correction.')).toBeDefined();
    expect(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: 'Not this ad' })).toBeDefined();
  });

  it('plays the match span through the preview route', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    renderList();
    const row = (await screen.findAllByRole('listitem'))[1];
    await userEvent.click(within(row).getByRole('button', { name: /^Play/ }));
    expect(document.querySelector('audio')?.getAttribute('src')).toBe('/api/v1/patterns/7/audio/preview?matchId=102');
  });

  it('says why a match cannot play', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('no'));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 404, json: async () => ({ code: 'original_not_retained' }),
    } as unknown as Response));
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    await userEvent.click(within(row).getByRole('button', { name: /^Play/ }));
    expect(await within(row).findByText("This episode's original audio is no longer kept.")).toBeDefined();
    vi.unstubAllGlobals();
  });

  it('shows an empty state', async () => {
    mockGetMatches.mockResolvedValue({ matches: [], page: 1, perPage: 10, total: 0 });
    renderList();
    expect(await screen.findByText('No matches yet.')).toBeDefined();
  });

  it('shows a loading status', () => {
    mockGetMatches.mockReturnValue(new Promise(() => {}));
    renderList();
    expect(screen.getByRole('status', { name: 'Loading matches' })).toBeDefined();
  });

  it('shows an error with Retry instead of an empty list', async () => {
    mockGetMatches.mockRejectedValueOnce(new Error('boom'));
    renderList();
    expect(await screen.findByText('Could not load matches.')).toBeDefined();
    expect(screen.queryByText('No matches yet.')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findAllByRole('listitem')).toHaveLength(10);
  });

  it('names an episode that no longer exists', async () => {
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1, { episodeTitle: null, available: false })], 1));
    renderList();
    expect(await screen.findByText('Episode no longer available')).toBeDefined();
  });
});
