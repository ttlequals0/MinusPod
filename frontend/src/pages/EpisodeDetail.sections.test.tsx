// Episode page sections are tabs: Ads (with in-tab jump links), Transcript, Processing.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import EpisodeDetail from './EpisodeDetail';
import { getEpisode, getFeed, getOriginalSegments } from '../api/feeds';
import type { EpisodeDetail as EpisodeDetailType } from '../api/types';

vi.mock('../components/AdEditor', () => ({
  default: () => <input data-testid="ad-draft" aria-label="Ad draft" />,
}));
vi.mock('../components/PatternLink', () => ({ default: ({ reason }: { reason: string }) => <span>{reason}</span> }));
vi.mock('../components/CueDetectionsSection', () => ({ default: () => <div data-testid="cue-detections" /> }));
vi.mock('../components/CueCandidatesSection', () => ({ default: () => <div data-testid="cue-candidates" /> }));
vi.mock('../components/PrevNextLink', () => ({ default: () => null }));
vi.mock('../components/Artwork', () => ({ default: ({ alt }: { alt: string }) => <img alt={alt} /> }));
vi.mock('../components/ProcessingRunsTable', () => ({ default: () => <div data-testid="runs-table" /> }));
vi.mock('../components/EpisodeLogsCard', () => ({ default: () => <div data-testid="logs-card" /> }));
vi.mock('../hooks/useLocalStorageState', () => ({
  useLocalStorageState: (_key: string, initial: unknown) => [initial, vi.fn()],
  readStoredValue: (_key: string, fallback: unknown) => fallback,
}));
vi.mock('../api/feeds', () => ({
  getEpisode: vi.fn(),
  getFeed: vi.fn(),
  getOriginalSegments: vi.fn(),
  getFinalSegments: vi.fn(),
  getOriginalTranscript: vi.fn(),
  rateEpisode: vi.fn().mockResolvedValue({ rating: 'up' }),
  clearEpisodeRating: vi.fn(),
  episodeOriginalUrl: (slug: string, id: string) => `/api/v1/feeds/${slug}/episodes/${id}/original.mp3`,
}));
vi.mock('../api/patterns', () => ({
  submitCorrection: vi.fn(),
  getPatterns: vi.fn(),
  rebuildPatternAudio: vi.fn(),
  describeFingerprintSkip: (reason: string) => reason,
  invalidatePatternAudio: () => {},
}));
vi.mock('../api/settings', async (importActual) => ({
  APPROVAL_INVALIDATE_KEYS: (await importActual<typeof import('../api/settings')>()).APPROVAL_INVALIDATE_KEYS,
  getSettings: vi.fn().mockResolvedValue({ llmProvider: { value: 'anthropic' } }),
}));

const held = {
  start: 120, end: 360, confidence: 0.95, reason: 'sponsor match', held_for_review: true,
  hold_reason: 'max_duration' as const,
  validation: { decision: 'REVIEW' as const, adjusted_confidence: 0.95, flags: ['duration_cap'] },
};
const run = {
  runNumber: 1, processedAt: '2026-01-01T00:00:00Z', status: 'completed' as const, adsDetected: 1,
  processingDurationSeconds: 10, errorMessage: null, inputTokens: 1, outputTokens: 1, llmCost: 0, stats: null,
};

function episode(overrides: Partial<EpisodeDetailType> = {}): EpisodeDetailType {
  return {
    id: 'ep-1', title: 'Test Episode', published: '2026-01-01T00:00:00Z', status: 'completed',
    processedAt: '2026-01-01T00:00:00Z', hasOriginalAudio: true, corrections: [],
    adMarkers: [{ start: 10, end: 40, confidence: 0.9, reason: 'Sponsor A' }],
    pendingReviewMarkers: [held], transcript: 'Hello world', originalTranscriptAvailable: true,
    processingRuns: [run, { ...run, runNumber: 2 }],
    ...overrides,
  } as EpisodeDetailType;
}

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.search}</output>;
}

function renderAt(entry: string, ep: EpisodeDetailType = episode()) {
  vi.mocked(getEpisode).mockResolvedValue(ep);
  vi.mocked(getFeed).mockResolvedValue({ slug: 'test-feed', title: 'Feed', artworkUrl: null } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Where />
        <Routes><Route path="/feeds/:slug/episodes/:episodeId" element={<EpisodeDetail />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const BASE = '/feeds/test-feed/episodes/ep-1';
const panel = (name: string) => document.getElementById(`episode-sections-panel-${name}`) as HTMLElement;
const selected = () => screen.getAllByRole('tab').filter((t) => t.getAttribute('aria-selected') === 'true').map((t) => t.id);

describe('EpisodeDetail tabs', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getOriginalSegments).mockResolvedValue({
      episodeId: 'ep-1',
      segments: [{ start: 0, end: 10, text: 'Welcome to the show.' }, { start: 10, end: 40, text: 'Brought to you by Acme.' }],
    } as never);
  });

  it('opens on Ads with the held count, and only that panel visible', async () => {
    renderAt(BASE);
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['Ads1', 'Transcript', 'Processing2']);
    expect(selected()).toEqual(['episode-sections-tab-ads']);
    expect(panel('ads').hidden).toBe(false);
    expect(panel('transcript').hidden).toBe(true);
    expect(panel('processing').hidden).toBe(true);
    const jump = screen.getByRole('navigation', { name: 'Ad sections' });
    expect(within(jump).getAllByRole('link').map((a) => a.getAttribute('href')))
      .toEqual(['#detected-ads-section', '#held-for-review-section']);
  });

  it('puts Transcript first when there are no detections, and drops the bar for one tab', async () => {
    const { unmount } = renderAt(BASE, episode({ adMarkers: [], pendingReviewMarkers: [] }));
    await screen.findAllByRole('tab');
    expect(selected()).toEqual(['episode-sections-tab-transcript']);
    unmount();
    renderAt(BASE, episode({ adMarkers: [], pendingReviewMarkers: [], processingRuns: [] }));
    await screen.findByText('Test Episode');
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(await screen.findByLabelText('Search')).toBeTruthy();
  });

  it('writes ?section= when a tab is chosen', async () => {
    renderAt(BASE);
    await userEvent.click(await screen.findByRole('tab', { name: /Processing/ }));
    expect(screen.getByTestId('where').textContent).toBe('?section=processing');
    expect(selected()).toEqual(['episode-sections-tab-processing']);
    expect(within(panel('processing')).getByTestId('runs-table')).toBeTruthy();
    expect(within(panel('processing')).getByTestId('logs-card')).toBeTruthy();
  });

  it.each([
    ['held-for-review-section', 'ads'], ['detected-ads-section', 'ads'], ['kept-segments-section', 'ads'],
    ['detections-not-cut-section', 'ads'], ['cue-matches-section', 'ads'],
    ['transcript-section', 'transcript'], ['processing-stats-section', 'processing'], ['logs-section', 'processing'],
  ])('maps the old #%s link to the %s tab', async (hash, tab) => {
    renderAt(`${BASE}#${hash}`);
    await screen.findAllByRole('tab');
    expect(selected()).toEqual([`episode-sections-tab-${tab}`]);
  });

  it('ignores an unknown section value', async () => {
    renderAt(`${BASE}?section=bogus`);
    await screen.findAllByRole('tab');
    expect(selected()).toEqual(['episode-sections-tab-ads']);
  });

  it('keeps the ad editor draft and the transcript search across tab switches', async () => {
    renderAt(BASE);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Edit ads' }));
    await user.type(await screen.findByLabelText('Ad draft'), 'my draft');
    await user.click(screen.getByRole('tab', { name: 'Transcript' }));
    expect(panel('ads').hidden).toBe(true);
    await user.type(await screen.findByLabelText('Search'), 'acme');
    await user.click(screen.getByRole('tab', { name: /Ads/ }));
    expect((screen.getByLabelText('Ad draft') as HTMLInputElement).value).toBe('my draft');
    await user.click(screen.getByRole('tab', { name: 'Transcript' }));
    expect((screen.getByLabelText('Search') as HTMLInputElement).value).toBe('acme');
  });

  it('lazy-loads the transcript only on its first visit', async () => {
    renderAt(BASE);
    await screen.findAllByRole('tab');
    expect(getOriginalSegments).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('tab', { name: 'Transcript' }));
    expect(await screen.findByText('Welcome to the show.')).toBeTruthy();
    expect(getOriginalSegments).toHaveBeenCalledTimes(1);
  });

  it('opens the Transcript tab at the second from a search link and flashes that line', async () => {
    Element.prototype.scrollIntoView = vi.fn();
    renderAt(`${BASE}?section=transcript&t=12`);
    const row = (await screen.findByText('Brought to you by Acme.')).closest('li');
    expect(selected()).toEqual(['episode-sections-tab-transcript']);
    await vi.waitFor(() => expect(row?.className).toContain('bg-primary/20'));
  });

  it('keeps the rating card at the bottom of the Ads tab only', async () => {
    renderAt(BASE);
    await screen.findAllByRole('tab');
    expect(panel('ads').querySelector('#episode-rating-card')).not.toBeNull();
    await userEvent.click(screen.getByRole('tab', { name: 'Transcript' }));
    expect(panel('ads').hidden).toBe(true);
    expect(document.querySelectorAll('#episode-rating-card')).toHaveLength(1);
  });

  it('shows the rating card below the page when there is no Ads tab', async () => {
    renderAt(BASE, episode({ adMarkers: [], pendingReviewMarkers: [], rejectedAdMarkers: [] }));
    await screen.findAllByRole('tab');
    const card = document.getElementById('episode-rating-card')!;
    expect(card).not.toBeNull();
    expect(card.closest('[role=tabpanel]')).toBeNull();
  });

  it('opens the Ads tab and scrolls to the rating card for a review link', async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    renderAt(`${BASE}?section=transcript&rate=up`);
    await screen.findAllByRole('tab');
    expect(selected()).toEqual(['episode-sections-tab-ads']);
    await vi.waitFor(() => expect(scroll).toHaveBeenCalled());
  });

  it('leaves no focusable control in a hidden panel', async () => {
    renderAt(BASE);
    await userEvent.click(await screen.findByRole('tab', { name: 'Transcript' }));
    expect(panel('ads').hidden).toBe(true);
    expect(within(panel('ads')).queryAllByRole('button')).toEqual([]);
  });
});
