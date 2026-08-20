import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RunLogViewer from './RunLogViewer';

const mockGetEpisodeRunLog = vi.fn();
vi.mock('../api/feeds', () => ({
  getEpisodeRunLog: (...args: unknown[]) => mockGetEpisodeRunLog(...args),
  episodeRunLogDownloadUrl: (slug: string, episodeId: string, runNumber: number) =>
    `/api/v1/feeds/${slug}/episodes/${episodeId}/runs/${runNumber}/log?format=raw`,
}));

const LINES = [
  { ts: '2026-08-19T12:00:00.000Z', level: 'DEBUG', logger: 'podcast.audio', msg: 'window 1 prompt built' },
  { ts: '2026-08-19T12:00:01.000Z', level: 'INFO', logger: 'podcast.audio', msg: 'Starting sweep' },
  { ts: '2026-08-19T12:00:02.000Z', level: 'WARNING', logger: 'podcast.claude', msg: 'window 2 retried' },
  { ts: '2026-08-19T12:00:03.000Z', level: 'ERROR', logger: 'podcast.audio', msg: 'cut failed' },
];

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
}

function renderViewer(onClose = vi.fn()) {
  return render(
    <QueryClientProvider client={makeClient()}>
      <RunLogViewer slug="test-feed" episodeId="abc123def456" runNumber={2} onClose={onClose} />
    </QueryClientProvider>,
  );
}

describe('RunLogViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetEpisodeRunLog.mockResolvedValue({
      runNumber: 2, lines: LINES, truncated: false, bytes: 4096,
    });
  });

  it('renders every line of the log', async () => {
    renderViewer();
    expect(await screen.findByText('Starting sweep')).toBeTruthy();
    expect(screen.getByText('window 1 prompt built')).toBeTruthy();
    expect(screen.getByText('cut failed')).toBeTruthy();
  });

  it('shows every level until a chip is selected', async () => {
    renderViewer();
    await screen.findByText('Starting sweep');
    expect(screen.getByRole('button', { name: 'Debug' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByText('window 1 prompt built')).toBeTruthy();
    expect(screen.getByText('cut failed')).toBeTruthy();
  });

  it('keeps only the selected levels', async () => {
    renderViewer();
    await screen.findByText('Starting sweep');
    await userEvent.click(screen.getByRole('button', { name: 'Warning' }));
    await userEvent.click(screen.getByRole('button', { name: 'Error' }));
    await waitFor(() => expect(screen.queryByText('Starting sweep')).toBeNull());
    expect(screen.queryByText('window 1 prompt built')).toBeNull();
    expect(screen.getByText('window 2 retried')).toBeTruthy();
    expect(screen.getByText('cut failed')).toBeTruthy();
  });

  it('clears a level filter when its chip is clicked again', async () => {
    renderViewer();
    await screen.findByText('Starting sweep');
    const chip = screen.getByRole('button', { name: 'Error' });
    await userEvent.click(chip);
    await waitFor(() => expect(screen.queryByText('Starting sweep')).toBeNull());
    await userEvent.click(chip);
    expect(await screen.findByText('Starting sweep')).toBeTruthy();
    expect(chip.getAttribute('aria-pressed')).toBe('false');
  });

  it('filters by search text', async () => {
    renderViewer();
    await screen.findByText('Starting sweep');
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search this log' }), 'retried');
    await waitFor(() => expect(screen.queryByText('Starting sweep')).toBeNull());
    expect(screen.getByText('window 2 retried')).toBeTruthy();
  });

  it('shows critical under the error chip and never hides unfamiliar levels', async () => {
    mockGetEpisodeRunLog.mockResolvedValue({
      runNumber: 2,
      lines: [
        ...LINES,
        { ts: '2026-08-19T12:00:04.000Z', level: 'CRITICAL', logger: 'podcast.audio', msg: 'process is going down' },
        { ts: '2026-08-19T12:00:05.000Z', level: 'AUDIT', logger: 'podcast.audio', msg: 'unfamiliar level' },
      ],
      truncated: false,
      bytes: 4096,
    });
    renderViewer();
    await screen.findByText('Starting sweep');

    await userEvent.click(screen.getByRole('button', { name: 'Error' }));

    expect(screen.getByText('process is going down')).toBeTruthy();
    expect(screen.getByText('unfamiliar level')).toBeTruthy();
    expect(screen.queryByText('Starting sweep')).toBeNull();
  });

  it('reports how many lines are showing', async () => {
    renderViewer();
    await screen.findByText('Starting sweep');
    await userEvent.click(screen.getByRole('button', { name: 'Error' }));
    expect(await screen.findByText('1 of 4 lines')).toBeTruthy();
  });

  it('says when nothing matches the filters', async () => {
    renderViewer();
    await screen.findByText('Starting sweep');
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search this log' }), 'zzz');
    expect(await screen.findByText(/No lines match/)).toBeTruthy();
  });

  it('links the download to the raw file', async () => {
    renderViewer();
    const link = await screen.findByRole('link', { name: 'Download' });
    expect(link.getAttribute('href')).toBe(
      '/api/v1/feeds/test-feed/episodes/abc123def456/runs/2/log?format=raw');
  });

  it('flags a log that hit the size cap', async () => {
    mockGetEpisodeRunLog.mockResolvedValue({
      runNumber: 2, lines: LINES, truncated: true, bytes: 20971520,
    });
    renderViewer();
    expect(await screen.findByText(/hit the 20 MB cap/)).toBeTruthy();
  });

  it('explains a log that is no longer on disk', async () => {
    mockGetEpisodeRunLog.mockRejectedValue(new Error('Run log is no longer available'));
    renderViewer();
    expect(await screen.findByText(/Run log is no longer available/)).toBeTruthy();
  });

  it('closes on the close button', async () => {
    const onClose = vi.fn();
    renderViewer(onClose);
    await screen.findByText('Starting sweep');
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalled();
  });
});
