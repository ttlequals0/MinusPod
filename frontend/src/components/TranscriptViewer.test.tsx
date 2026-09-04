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
    mockOriginal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: SEGS });
    mockFinal.mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: [SEGS[0], SEGS[2]] });
  });

  it('shows original segments with timestamps and filters by search', async () => {
    renderViewer();
    await waitFor(() => screen.getByText('Welcome to the show.'));
    expect(mockOriginal).toHaveBeenCalledWith('example-podcast', 'a1b2c3d4e5f6');
    expect(screen.getByText('0:10 - 0:40')).toBeTruthy();
    await userEvent.type(screen.getByLabelText('Search transcript'), 'acme');
    expect(screen.queryByText('Welcome to the show.')).toBeNull();
    expect(screen.getByText(/brought to you by/)).toBeTruthy();
    expect(screen.getByText('Acme', { selector: 'mark' })).toBeTruthy();
    expect(screen.getByText('1 of 3 segments')).toBeTruthy();
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
