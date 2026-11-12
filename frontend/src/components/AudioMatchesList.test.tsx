import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AudioMatchesList from './AudioMatchesList';
import { usePatternAudioPreview } from '../hooks/usePatternAudioPreview';
import type { AudioMatch } from '../api/patterns';
import { iconTarget, iconVisual, rowActionBtn } from './rowActionStyles';

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
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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

  it('gives each row action a name that says which episode it acts on', async () => {
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    expect(within(row).getByRole('button', { name: 'Not this ad, Episode 1' })).toBeDefined();
    expect(within(row).getByRole('button', { name: 'Play match at 5:01, Episode 1' })).toBeDefined();
  });

  it('labels the section from its heading and keeps no inert padding on the row card', async () => {
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    expect(screen.getByRole('region', { name: 'Recent matches' })).toBeDefined();
    expect(row.className).not.toMatch(/(^| )p-3( |$)/);
  });

  it('keeps seconds for spans up to ten minutes so the two numbers compare', async () => {
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1, { start: 100, end: 175, storedLength: 90 }), makeMatch(2, { start: 100, end: 800, storedLength: 700 })], 1));
    renderList();
    const rows = await screen.findAllByRole('listitem');
    expect(within(rows[0]).getByText('75s of 90s')).toBeDefined();
    expect(within(rows[1]).getByText('11m 40s of 11m 40s')).toBeDefined();
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
    expect(within(row).queryByRole('button', { name: /^Not this ad/ })).toBeNull();
    expect(within(row).getByRole('button', { name: /^Play/ })).toBeDefined();
  });

  it('sends a reject correction with the measured span and marks the row', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    const { invalidateSpy } = renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    mockGetMatches.mockImplementation((_id: number, p: { page?: number } = {}) => Promise.resolve(
      pageOf(twelve.map((m) => (m.id === 101 ? { ...m, rejected: true } : m)), p.page ?? 1)));
    await userEvent.click(within(row).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(mockSubmitCorrection).toHaveBeenCalledWith('example-podcast', 'ep1', {
      type: 'reject', original_ad: { start: 301, end: 339, pattern_id: 7 },
    }));
    await waitFor(() => expect(within(screen.getAllByRole('listitem')[0]).queryByRole('button', { name: /^Not this ad/ })).toBeNull());
    expect(within(screen.getAllByRole('listitem')[0]).getByText('Not this ad')).toBeDefined();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pattern-audio', 7] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['patterns'], refetchType: 'none' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pattern-audio-matches', 7] });
  });

  it('refreshes the detections and the episode the way the other correction flows do', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    const { invalidateSpy } = renderList();
    await userEvent.click(within((await screen.findAllByRole('listitem'))[0]).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['detections'], refetchType: 'none' }));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['episode', 'example-podcast', 'ep1'], refetchType: 'none' });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['pending-recuts'] });
  });

  it('refetches only the audio detail and matches on screen, leaving the lists stale until they are shown again', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const listFetch = vi.fn().mockResolvedValue([]);
    const detailFetch = vi.fn().mockResolvedValue({ state: 'active' });
    function Watchers() {
      useQuery({ queryKey: ['patterns', { active: true }], queryFn: listFetch });
      useQuery({ queryKey: ['detections', {}], queryFn: listFetch });
      useQuery({ queryKey: ['episode', 'example-podcast', 'ep1'], queryFn: listFetch });
      useQuery({ queryKey: ['pattern-audio', 7], queryFn: detailFetch });
      return null;
    }
    render(<QueryClientProvider client={qc}><Watchers /><Harness /></QueryClientProvider>);
    const row = (await screen.findAllByRole('listitem'))[0];
    await waitFor(() => expect(detailFetch).toHaveBeenCalledTimes(1));
    listFetch.mockClear();
    mockGetMatches.mockClear();
    await userEvent.click(within(row).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(detailFetch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockGetMatches).toHaveBeenCalledTimes(1));
    expect(listFetch).not.toHaveBeenCalled();
    for (const key of [['patterns'], ['detections'], ['episode', 'example-podcast', 'ep1']]) {
      expect(qc.getQueryCache().findAll({ queryKey: key }).every((q) => q.state.isInvalidated)).toBe(true);
    }
  });

  it('moves focus to the row play button after Not this ad, so it is not dropped on the page', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    mockGetMatches.mockImplementation((_id: number, p: { page?: number } = {}) => Promise.resolve(
      pageOf(twelve.map((m) => (m.id === 101 ? { ...m, rejected: true } : m)), p.page ?? 1)));
    await userEvent.click(within(row).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(document.activeElement).toBe(
      within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /^Play match/ })));
  });

  it('does not pull focus back to a rejected row on later renders', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    renderList();
    const rows = await screen.findAllByRole('listitem');
    mockGetMatches.mockImplementation((_id: number, p: { page?: number } = {}) => Promise.resolve(
      pageOf(twelve.map((m) => (m.id === 101 ? { ...m, rejected: true } : m)), p.page ?? 1)));
    await userEvent.click(within(rows[0]).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(document.activeElement).toBe(
      within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /^Play match/ })));
    const other = within(screen.getAllByRole('listitem')[2]).getByRole('button', { name: /^Play match/ });
    await userEvent.click(other);
    expect(document.activeElement).toBe(other);
  });

  it('falls back to the row heading when the row has no play button', async () => {
    mockSubmitCorrection.mockResolvedValue(undefined);
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1)], 1));
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    const play = within(row).getByRole('button', { name: /^Play match/ });
    mockGetMatches.mockResolvedValue(pageOf([makeMatch(1, { rejected: true, available: false })], 1));
    await userEvent.click(within(row).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(play.isConnected).toBe(false));
    await waitFor(() => expect(document.activeElement?.tagName).toBe('H5'));
    expect(document.activeElement?.textContent).toBe('Episode 1');
  });

  it('shows the server message when the correction fails and keeps the row actionable', async () => {
    mockSubmitCorrection.mockRejectedValue(new Error('Episode is being processed'));
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    await userEvent.click(within(row).getByRole('button', { name: /^Not this ad/ }));
    expect((await screen.findByRole('alert')).textContent).toBe('Episode is being processed');
    expect(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /^Not this ad/ })).toBeDefined();
  });

  it('clears the failure on a page change and after a later success', async () => {
    mockSubmitCorrection.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(undefined);
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    await userEvent.click(within(row).getByRole('button', { name: /^Not this ad/ }));
    expect((await screen.findByRole('alert')).textContent).toBe('boom');
    await userEvent.click(within(screen.getAllByRole('listitem')[0]).getByRole('button', { name: /^Not this ad/ }));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    mockSubmitCorrection.mockRejectedValueOnce(new Error('again'));
    await userEvent.click(within(screen.getAllByRole('listitem')[2]).getByRole('button', { name: /^Not this ad/ }));
    expect((await screen.findByRole('alert')).textContent).toBe('again');
    await userEvent.click(screen.getAllByRole('button', { name: 'Page 2' })[0]);
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('shows a pending state and disables the old rows while the next page loads', async () => {
    renderList();
    await screen.findAllByRole('listitem');
    let finish: (v: unknown) => void = () => {};
    mockGetMatches.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await userEvent.click(screen.getAllByRole('button', { name: 'Page 2' })[0]);
    const list = screen.getAllByRole('list')[0];
    await waitFor(() => expect(list.getAttribute('aria-busy')).toBe('true'));
    expect(screen.getByText('Loading matches...').getAttribute('role')).toBe('status');
    for (const button of within(list).getAllByRole('button')) expect((button as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { finish(pageOf(twelve, 2)); });
    await waitFor(() => expect(list.getAttribute('aria-busy')).toBe('false'));
    expect(screen.queryByText('Loading matches...')).toBeNull();
  });

  it('plays a match from its preview url and shows Loading audio until it starts', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    renderList();
    const row = (await screen.findAllByRole('listitem'))[1];
    await userEvent.click(within(row).getByRole('button', { name: /^Play/ }));
    expect(within(row).getByRole('status').textContent).toBe('Loading audio...');
    expect(document.querySelector('audio')?.getAttribute('src')).toBe('/api/v1/patterns/7/audio/preview?matchId=102');
    fireEvent.playing(document.querySelector('audio') as HTMLAudioElement);
    expect(within(row).queryByRole('status')).toBeNull();
  });

  it('says why a match cannot play, and a retry makes a new request', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const fetchSpy = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: 'original_not_retained' }), { status: 404 }));
    vi.stubGlobal('fetch', fetchSpy);
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    await userEvent.click(within(row).getByRole('button', { name: /^Play/ }));
    fireEvent.error(document.querySelector('audio') as HTMLAudioElement);
    expect(await within(row).findByText("This episode's original audio is no longer kept.")).toBeDefined();
    await userEvent.click(within(row).getByRole('button', { name: /^Play/ }));
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(within(row).queryByText("This episode's original audio is no longer kept.")).toBeNull());
  });

  it('sizes the play button and Not this ad from the same row-action recipe', async () => {
    renderList();
    const row = (await screen.findAllByRole('listitem'))[0];
    const play = within(row).getByRole('button', { name: /^Play/ });
    const not = within(row).getByRole('button', { name: /^Not this ad/ });
    for (const token of iconTarget.split(' ')) expect(play.className).toContain(token);
    for (const token of rowActionBtn.split(' ')) expect(not.className).toContain(token);
    // The painted square follows the target on coarse pointers at every width.
    expect(iconVisual).toContain('sm:[@media(pointer:coarse)]:h-11');
    expect(iconVisual).toContain('h-11 w-11');
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
