import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SponsorBlockSubmit from './SponsorBlockSubmit';

const mockGetCandidates = vi.fn();

vi.mock('../api/feeds', () => ({
  episodeOriginalUrl: (slug: string, id: string) => `/api/v1/feeds/${slug}/episodes/${id}/original.mp3`,
  getSponsorBlockCandidates: (...args: unknown[]) => mockGetCandidates(...args),
  submitSponsorBlock: vi.fn(),
}));

const seg = (start: number, end: number) => ({
  start, end, category: 'sponsor', sbCategory: 'sponsor', evidence: [], submission: null,
});

async function openDialog() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SponsorBlockSubmit slug="example-podcast" episodeId="a1b2c3d4e5f6" />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Submit to SponsorBlock' }));
  await screen.findByRole('dialog');
}

describe('SponsorBlockSubmit', () => {
  let play: MockInstance<() => Promise<void>>;
  let pause: MockInstance<() => void>;

  beforeEach(() => {
    play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    mockGetCandidates.mockReset().mockResolvedValue({
      allowed: true, reason: null, segments: [seg(10, 40), seg(125, 150)],
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it('explains service_down', async () => {
    mockGetCandidates.mockResolvedValue({ allowed: false, reason: 'service_down', segments: [] });
    await openDialog();
    expect(await screen.findByText('SponsorBlock is unreachable right now. Try again later.')).toBeTruthy();
  });

  it('gives each candidate a play button that names the span', async () => {
    await openDialog();
    expect(await screen.findByRole('button', { name: 'Play segment 0:10 to 0:40' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play segment 2:05 to 2:30' })).toBeTruthy();
  });

  it('does not toggle the checkbox when play is clicked', async () => {
    await openDialog();
    const box = (await screen.findAllByRole('checkbox'))[0] as HTMLInputElement;
    fireEvent.click(screen.getByRole('button', { name: 'Play segment 0:10 to 0:40' }));
    expect(box.checked).toBe(false);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('stops the first segment when a second one starts', async () => {
    await openDialog();
    fireEvent.click(await screen.findByRole('button', { name: 'Play segment 0:10 to 0:40' }));
    await screen.findByRole('button', { name: 'Pause segment 0:10 to 0:40' });
    fireEvent.click(screen.getByRole('button', { name: 'Play segment 2:05 to 2:30' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause segment 2:05 to 2:30' })).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Play segment 0:10 to 0:40' })).toBeTruthy();
  });

  it('pauses audio when the dialog closes', async () => {
    await openDialog();
    fireEvent.click(await screen.findByRole('button', { name: 'Play segment 0:10 to 0:40' }));
    pause.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(pause).toHaveBeenCalled();
  });
});
