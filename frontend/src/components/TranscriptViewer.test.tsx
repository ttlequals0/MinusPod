import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TranscriptViewer from './TranscriptViewer';
import type { EpisodeDetail } from '../api/types';
import { ApiError } from '../api/client';

const mockOriginal = vi.fn();
const mockFinal = vi.fn();
const mockOriginalTranscript = vi.fn();
vi.mock('../api/feeds', () => ({
  getOriginalSegments: (...a: unknown[]) => mockOriginal(...a),
  getFinalSegments: (...a: unknown[]) => mockFinal(...a),
  getOriginalTranscript: (...a: unknown[]) => mockOriginalTranscript(...a),
}));

const SEGS = [
  { start: 0, end: 10, text: 'Welcome to the show.' },
  { start: 10, end: 40, text: 'This episode is brought to you by Acme.' },
  { start: 40, end: 60, text: 'Back to batteries.' },
];
const episode = {
  id: 'a1b2c3d4e5f6', title: 'Batteries again', transcript: 'plain text only',
  originalTranscriptAvailable: true,
  adMarkers: [{ start: 10, end: 40, confidence: 0.9, sponsor: 'Acme' }],
} as unknown as EpisodeDetail;

function renderViewer(ep: EpisodeDetail = episode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TranscriptViewer slug="example-podcast" episodeId="a1b2c3d4e5f6" episode={ep} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('TranscriptViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem('minuspod.transcript.view', 'lines');
    mockOriginal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: SEGS });
    mockFinal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: [SEGS[0], SEGS[2]] });
  });

  it('shows original segments with timestamps and filters by search', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    expect(mockOriginal).toHaveBeenCalledWith('example-podcast', 'a1b2c3d4e5f6');
    expect(screen.getByText('0:10 - 0:40')).toBeTruthy();
    await userEvent.type(screen.getByLabelText('Search'), 'acme');
    expect(screen.queryByText('Welcome to the show.')).toBeNull();
    expect(screen.getByText(/brought to you by/)).toBeTruthy();
    expect(screen.getByText('Acme', { selector: 'mark' })).toBeTruthy();
    expect(screen.getByText('1 match in 3 segments')).toBeTruthy();
  });

  it('filters by time window and flags an unparsable time', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    const start = screen.getByLabelText('Start');
    expect(start.getAttribute('aria-invalid')).toBe('false');
    await userEvent.type(start, '0:41');
    expect(screen.queryByText('Welcome to the show.')).toBeNull();
    expect(screen.getByText('Back to batteries.')).toBeTruthy();
    await userEvent.clear(start);
    await userEvent.type(start, 'x');
    expect(start.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Welcome to the show.')).toBeTruthy();
  });

  it('highlights ad rows only in original mode', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    await userEvent.click(screen.getByLabelText('Highlight ads'));
    expect(screen.getByText('Acme')).toBeTruthy();
    const row = screen.getByText(/brought to you by/).closest('li');
    expect(row?.className).toContain('border-l-destructive');
    await userEvent.selectOptions(screen.getByLabelText('Source'), 'processed');
    await waitFor(() => expect(screen.queryByText(/brought to you by/)).toBeNull());
    expect(mockFinal).toHaveBeenCalledWith('example-podcast', 'a1b2c3d4e5f6');
    expect(screen.queryByLabelText('Highlight ads')).toBeNull();
  });

  it('falls back to plain text when processed segments 404', async () => {
    mockFinal.mockRejectedValue(new ApiError('Final segments not found', 404));
    renderViewer({ ...episode, originalTranscriptAvailable: false } as EpisodeDetail);
    await waitFor(() => screen.getByText('plain text only'));
    expect(mockOriginal).not.toHaveBeenCalled();
    expect((screen.getByLabelText('Source') as HTMLSelectElement).value).toBe('processed');
  });

  it('shows the error message instead of plain text on a non-404 processed error', async () => {
    mockFinal.mockRejectedValue(new ApiError('Something broke', 500));
    renderViewer({ ...episode, originalTranscriptAvailable: false } as EpisodeDetail);
    await waitFor(() => screen.getByText('Something broke'));
    expect(screen.queryByText('plain text only')).toBeNull();
  });

  it('falls back to the original transcript text on a 404 for original segments', async () => {
    mockOriginal.mockRejectedValue(new ApiError('Original segments not found', 404));
    mockOriginalTranscript.mockResolvedValue('the retained pre-cut transcript');
    renderViewer();
    await waitFor(() => screen.getByText('the retained pre-cut transcript'));
    expect(mockOriginalTranscript).toHaveBeenCalledWith('example-podcast', 'a1b2c3d4e5f6');
    expect(screen.getByText('Plain text, no timestamps')).toBeTruthy();
  });
});

describe('TranscriptViewer loading placeholder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockOriginal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: SEGS });
    mockFinal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: [SEGS[0], SEGS[2]] });
  });

  it('shows segment skeletons while the transcript query is pending', async () => {
    mockOriginal.mockReturnValueOnce(new Promise(() => {}));
    renderViewer();
    expect(await screen.findByTestId('skeleton-rows')).toBeTruthy();
  });

  it('drops the skeletons once the segments land', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
  });
});

describe('TranscriptViewer paragraphs', () => {
  // Pauses of 2 s after segments 2 and 5 split the 7 segments into 3 paragraphs.
  const PSEGS = [
    { start: 0, end: 2, text: 'Welcome to the show.' },
    { start: 2, end: 4, text: 'Today we talk batteries.' },
    { start: 6, end: 8, text: 'This episode is brought to you by Acme.' },
    { start: 8, end: 10, text: 'Use code SAVE at checkout.' },
    { start: 12, end: 14, text: 'Back to batteries.' },
    { start: 14, end: 16, text: 'Lithium is everywhere.' },
  ];
  const adEpisode = {
    ...episode,
    adMarkers: [{ start: 8, end: 10, confidence: 0.9, sponsor: 'Acme' }],
  } as unknown as EpisodeDetail;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockOriginal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: PSEGS });
  });

  it('renders paragraphs by default with one start label each', async () => {
    renderViewer(adEpisode);
    await waitFor(() => screen.getByText(/Welcome to the show\./));
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(screen.getByText('0:00')).toBeTruthy();
    expect(screen.getByText('0:06')).toBeTruthy();
    expect(screen.getByText('0:12')).toBeTruthy();
    expect(screen.queryByText('0:00 - 0:02')).toBeNull();
    expect(screen.getAllByRole('listitem')[0].textContent).toContain('Welcome to the show. Today we talk batteries.');
  });

  it('switches to Lines for one row per segment and stores the choice', async () => {
    renderViewer(adEpisode);
    await waitFor(() => screen.getByText(/Welcome to the show\./));
    await userEvent.click(screen.getByRole('button', { name: 'Lines' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(PSEGS.length);
    expect(screen.getByText('0:00 - 0:02')).toBeTruthy();
    expect(localStorage.getItem('minuspod.transcript.view')).toBe('lines');
    await userEvent.click(screen.getByRole('button', { name: 'Paragraphs' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(localStorage.getItem('minuspod.transcript.view')).toBe('paragraphs');
  });

  it('restores a stored Lines choice', async () => {
    localStorage.setItem('minuspod.transcript.view', 'lines');
    renderViewer(adEpisode);
    await waitFor(() => screen.getByText('Welcome to the show.'));
    expect(screen.getAllByRole('listitem')).toHaveLength(PSEGS.length);
  });

  it('still filters by search per member and keeps the surviving paragraph', async () => {
    renderViewer(adEpisode);
    await waitFor(() => screen.getByText(/Welcome to the show\./));
    await userEvent.type(screen.getByLabelText('Search'), 'lithium');
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
    expect(screen.getByText('Lithium', { selector: 'mark' })).toBeTruthy();
    expect(screen.queryByText(/Back to batteries/)).toBeNull();
    expect(screen.getByText('1 match in 6 segments')).toBeTruthy();
  });

  it('highlights only the ad member inside its paragraph', async () => {
    renderViewer(adEpisode);
    await waitFor(() => screen.getByText(/Welcome to the show\./));
    await userEvent.click(screen.getByLabelText('Highlight ads'));
    const adSpan = screen.getByText('Use code SAVE at checkout.');
    expect(adSpan.className).toContain('bg-destructive/10');
    expect(adSpan.closest('li')?.className).not.toContain('bg-destructive');
    expect(screen.getByText('This episode is brought to you by Acme.').className).not.toContain('bg-destructive');
    expect(screen.getByText('Acme', { selector: 'span.shrink-0' })).toBeTruthy();
  });

  it('falls back to paragraphs when storage throws', async () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    try {
      renderViewer(adEpisode);
      await waitFor(() => screen.getByText(/Welcome to the show\./));
      expect(screen.getAllByRole('listitem')).toHaveLength(3);
      await userEvent.click(screen.getByRole('button', { name: 'Lines' }));
      expect(screen.getAllByRole('listitem')).toHaveLength(PSEGS.length);
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });
});

describe('TranscriptViewer search context and navigation', () => {
  const CSEGS = [
    { start: 0, end: 10, text: 'Welcome to the show.' },
    { start: 10, end: 20, text: 'Our blog has notes.' },
    { start: 20, end: 30, text: 'Next topic is batteries.' },
    { start: 30, end: 40, text: 'Lithium is everywhere.' },
    { start: 40, end: 50, text: 'Read the blog today.' },
    { start: 50, end: 60, text: 'Thanks for listening.' },
  ];
  const scroll = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('minuspod.transcript.view', 'lines');
    mockOriginal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: CSEGS });
    Element.prototype.scrollIntoView = scroll;
  });

  it('disables Context without a search and shows muted neighbours with it', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    expect((screen.getByRole('button', { name: '1' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Needs a search term')).toBeTruthy();
    expect(document.querySelector('[aria-describedby="transcript-context-hint"]')).not.toBeNull();
    await userEvent.type(screen.getByLabelText('Search'), 'blog');
    await userEvent.click(screen.getByRole('button', { name: '1' }));
    expect(localStorage.getItem('minuspod.transcript.context')).toBe('1');
    expect(screen.getByText('Welcome to the show.').className).toContain('text-muted-foreground');
    expect(screen.getAllByText('blog', { selector: 'mark' })).toHaveLength(2);
    expect(screen.getByText(/Our/).className).not.toContain('text-muted-foreground');
    expect(screen.queryByText('Lithium is everywhere.')).not.toBeNull();
    expect(screen.getByText('2 matches in 6 segments')).toBeTruthy();
  });

  it('adds neighbours to paragraphs before grouping', async () => {
    localStorage.setItem('minuspod.transcript.view', 'paragraphs');
    localStorage.setItem('minuspod.transcript.context', '1');
    renderViewer();
    await waitFor(() => screen.getByText(/Welcome to the show\./));
    await userEvent.type(screen.getByLabelText('Search'), 'blog');
    expect(screen.getByText('Welcome to the show.').className).toContain('text-muted-foreground');
  });

  it('jumps to the match in the full transcript and back', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    await userEvent.type(screen.getByLabelText('Search'), 'blog');
    expect(screen.queryByText('Welcome to the show.')).toBeNull();
    await userEvent.click(screen.getAllByRole('button', { name: /^Show in transcript/ })[1]);
    expect(screen.getByText('Welcome to the show.')).toBeTruthy();
    expect(screen.getByText('2 matches in 6 segments')).toBeTruthy();
    expect(scroll).toHaveBeenLastCalledWith({ block: 'start' });
    const row = document.querySelector('[data-row="4"]');
    expect(row?.className).toContain('bg-primary/20');
    expect(screen.getAllByText('blog', { selector: 'mark' })).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: 'Back to matches' }));
    expect(screen.queryByText('Welcome to the show.')).toBeNull();
    expect(screen.getByText('2 matches in 6 segments')).toBeTruthy();
  });

  it('cycles matches with Next and Previous and advances on Enter', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    const search = screen.getByLabelText('Search');
    await userEvent.type(search, 'blog');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Match 1 of 2')).toBeTruthy();
    expect(scroll).toHaveBeenLastCalledWith({ block: 'center' });
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Match 2 of 2')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Match 1 of 2')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(screen.getByText('Match 2 of 2')).toBeTruthy();
    await userEvent.type(search, '{Enter}');
    expect(screen.getByText('Match 1 of 2')).toBeTruthy();
  });

  it('counts one match per paragraph row in paragraphs view', async () => {
    localStorage.setItem('minuspod.transcript.view', 'paragraphs');
    mockOriginal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: [
      { start: 0, end: 2, text: 'Blog one.' },
      { start: 2, end: 4, text: 'Blog two.' },
      { start: 10, end: 12, text: 'Other blog.' },
    ] });
    renderViewer();
    await waitFor(() => screen.getByText(/Blog one\./));
    await userEvent.type(screen.getByLabelText('Search'), 'blog');
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Match 1 of 2')).toBeTruthy();
    expect(screen.getByText('3 matches in 3 segments')).toBeTruthy();
  });

  it('keeps the paged window when Context changes while browsing', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    await userEvent.type(screen.getByLabelText('Search'), 'blog');
    await userEvent.click(screen.getAllByRole('button', { name: /^Show in transcript/ })[0]);
    scroll.mockClear();
    await userEvent.click(screen.getByRole('button', { name: '3' }));
    expect(screen.getByText('Welcome to the show.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Back to matches' })).toBeTruthy();
  });

  it('uses the Start/End window as the footer denominator', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    await userEvent.type(screen.getByLabelText('Start'), '0:00');
    await userEvent.type(screen.getByLabelText('End'), '0:45');
    expect(screen.queryByText('Thanks for listening.')).toBeNull();
    await userEvent.type(screen.getByLabelText('Search'), 'blog');
    expect(screen.getByText('2 matches in 5 segments')).toBeTruthy();
  });
});
