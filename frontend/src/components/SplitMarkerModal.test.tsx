import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SplitMarkerModal from './SplitMarkerModal';

const mockGetCandidates = vi.fn();
const mockSubmitSplit = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  getSplitCandidates: (...a: unknown[]) => mockGetCandidates(...a),
  submitSplit: (...a: unknown[]) => mockSubmitSplit(...a),
}));
vi.mock('../api/sponsors', () => ({
  getSponsors: vi.fn().mockResolvedValue([]),
}));
vi.mock('./ad-editor/usePeaks', () => ({
  usePeaks: () => ({ peaks: [0.2, 0.5, 0.3], peakResolutionMs: 100, peaksError: null }),
}));

const TARGET = {
  podcastSlug: 'example-podcast',
  episodeId: 'a1b2c3d4e5f6',
  start: 100,
  end: 190,
};

function renderModal(onSplit = vi.fn(), onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SplitMarkerModal target={TARGET} onClose={onClose} onSplit={onSplit} />
    </QueryClientProvider>,
  );
  return { onSplit, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSubmitSplit.mockResolvedValue({
    message: 'Split into 2 ads', markerCount: 2, patternIds: [1, 2],
  });
  mockGetCandidates.mockResolvedValue({
    episodeId: TARGET.episodeId,
    start: 100,
    end: 190,
    candidates: [{ time: 130, phrase: 'brought to you by' }],
    pieces: [
      { start: 100, end: 130, text: 'Acme read', sponsor: 'Acme' },
      { start: 130, end: 190, text: 'Beta read', sponsor: 'Beta Corp' },
    ],
  });
});

describe('SplitMarkerModal', () => {
  it('seeds a divider from the server candidate', async () => {
    renderModal();
    await waitFor(() => expect(mockGetCandidates).toHaveBeenCalled());
    // Named specifically: ZoomControl's range input is also role=slider.
    expect(await screen.findByRole('slider', { name: /SPLIT/ })).toBeTruthy();
  });

  it('names the resulting ad count on the confirm button', async () => {
    renderModal();
    expect(await screen.findByRole('button', { name: 'Split into 2 ads' })).toBeTruthy();
  });

  it('adding a divider increases the piece count', async () => {
    renderModal();
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    await user.click(screen.getByRole('button', { name: 'Add divider' }));
    expect(await screen.findByRole('button', { name: 'Split into 3 ads' })).toBeTruthy();
  });

  it('adds a divider at the playhead, which is the only route on a phone', async () => {
    // Dragging is unreachable once the waveform is zoomed, so the button has
    // to place one. The playhead starts mid-block, well clear of both edges.
    renderModal();
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    const button = screen.getByRole('button', { name: /Add divider at playhead/ });
    expect(button.className).toContain('min-h-[44px]');
    await user.click(button);
    expect(await screen.findByRole('button', { name: 'Split into 3 ads' })).toBeTruthy();
  });

  it('a piece under the floor blocks the split and names it', async () => {
    mockGetCandidates.mockResolvedValue({
      episodeId: TARGET.episodeId, start: 100, end: 190,
      candidates: [{ time: 103, phrase: 'brought to you by' }],
      pieces: [
        { start: 100, end: 103, text: 'a', sponsor: null },
        { start: 103, end: 190, text: 'b', sponsor: null },
      ],
    });
    renderModal();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Ad 1 is 3.0s');
    expect(alert.textContent).toContain('4.0s short of the 7s minimum');
    expect(screen.getByRole('button', { name: 'Split into 2 ads' })
      .hasAttribute('disabled')).toBe(true);
  });

  it('submits sorted divider times and a sponsor per piece', async () => {
    const { onSplit } = renderModal();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Split into 2 ads' }));
    await waitFor(() => expect(mockSubmitSplit).toHaveBeenCalled());
    const [slug, episodeId, originalAd, points, pieces] = mockSubmitSplit.mock.calls[0];
    expect(slug).toBe('example-podcast');
    expect(episodeId).toBe('a1b2c3d4e5f6');
    expect(originalAd).toEqual({ start: 100, end: 190 });
    expect(points).toEqual([130]);
    expect(pieces).toEqual([{ sponsor: 'Acme' }, { sponsor: 'Beta Corp' }]);
    await waitFor(() => expect(onSplit).toHaveBeenCalled());
  });

  it('tells the user when no transition was found', async () => {
    mockGetCandidates.mockResolvedValue({
      episodeId: TARGET.episodeId, start: 100, end: 190,
      candidates: [],
      pieces: [{ start: 100, end: 190, text: 'one long read', sponsor: null }],
    });
    renderModal();
    expect(await screen.findByText(/No sponsor transition found/)).toBeTruthy();
  });

  it('cannot split with no dividers', async () => {
    mockGetCandidates.mockResolvedValue({
      episodeId: TARGET.episodeId, start: 100, end: 190,
      candidates: [],
      pieces: [{ start: 100, end: 190, text: 'one long read', sponsor: null }],
    });
    renderModal();
    const btn = await screen.findByRole('button', { name: 'Split into 1 ad' });
    expect(btn.hasAttribute('disabled')).toBe(true);
  });

  it('surfaces a save failure without closing', async () => {
    mockSubmitSplit.mockRejectedValue(new Error('boom'));
    const { onSplit } = renderModal();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Split into 2 ads' }));
    expect(await screen.findByText('Failed to split. Try again.')).toBeTruthy();
    expect(onSplit).not.toHaveBeenCalled();
  });

  it('renders one row per resulting ad with its own play control', async () => {
    renderModal();
    const rows = await screen.findByTestId('piece-rows');
    expect(within(rows).getAllByRole('button', { name: /play/i })).toHaveLength(2);
  });

  it('removing a divider drops back to one piece', async () => {
    renderModal();
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    await user.click(screen.getByRole('button', { name: 'Remove divider' }));
    expect(await screen.findByRole('button', { name: 'Split into 1 ad' })).toBeTruthy();
  });

  it('keeps a typed sponsor with its piece when a divider is added before it', async () => {
    mockGetCandidates.mockResolvedValue({
      episodeId: TARGET.episodeId, start: 100, end: 190,
      candidates: [{ time: 160, phrase: 'brought to you by' }],
      pieces: [
        { start: 100, end: 160, text: 'Acme read', sponsor: 'Acme' },
        { start: 160, end: 190, text: 'Beta read', sponsor: 'Beta Corp' },
      ],
    });
    renderModal();
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    const second = screen.getAllByPlaceholderText('Choose or type a sponsor')[1];
    await user.clear(second);
    await user.type(second, 'Custom Co');
    // The longest piece is the first, so the new divider lands before the
    // overridden piece and shifts it right.
    await user.click(screen.getByRole('button', { name: 'Add divider' }));
    await screen.findByRole('button', { name: 'Split into 3 ads' });
    const inputs = screen.getAllByPlaceholderText('Choose or type a sponsor') as HTMLInputElement[];
    expect(inputs[2].value).toBe('Custom Co');
    expect(inputs[1].value).toBe('Acme');
  });

  it('keeps a typed sponsor with its piece when a divider is removed', async () => {
    renderModal();
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    await user.click(screen.getByRole('button', { name: 'Add divider' }));
    await screen.findByRole('button', { name: 'Split into 3 ads' });
    const third = screen.getAllByPlaceholderText('Choose or type a sponsor')[2];
    await user.clear(third);
    await user.type(third, 'Custom Co');
    // Removing the first divider merges the first two pieces; the typed
    // sponsor stays with the last piece.
    await user.click(screen.getAllByRole('button', { name: 'Remove divider' })[0]);
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    const inputs = screen.getAllByPlaceholderText('Choose or type a sponsor') as HTMLInputElement[];
    expect(inputs).toHaveLength(2);
    expect(inputs[1].value).toBe('Custom Co');
    await user.click(screen.getByRole('button', { name: 'Split into 2 ads' }));
    await waitFor(() => expect(mockSubmitSplit).toHaveBeenCalled());
    expect(mockSubmitSplit.mock.calls[0][4]).toEqual(
      [{ sponsor: 'Acme' }, { sponsor: 'Custom Co' }]);
  });

  it('surfaces a candidates load failure with a retry', async () => {
    mockGetCandidates.mockRejectedValueOnce(new Error('boom'));
    renderModal();
    expect(await screen.findByText('Failed to load suggested dividers.')).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('button', { name: 'Split into 2 ads' })).toBeTruthy();
  });

  it('piece segments name the sponsor and clicking one focuses its input', async () => {
    renderModal();
    await screen.findByRole('button', { name: 'Split into 2 ads' });
    const segments = within(screen.getByTestId('piece-strip')).getAllByRole('button');
    expect(segments[1].textContent).toContain('Beta Corp');
    expect(segments[1].textContent).toContain('60s');
    const user = userEvent.setup();
    await user.click(segments[1]);
    const inputs = screen.getAllByPlaceholderText('Choose or type a sponsor');
    expect(document.activeElement).toBe(inputs[1]);
  });

  it('cancel closes without submitting', async () => {
    const onClose = vi.fn();
    renderModal(vi.fn(), onClose);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(mockSubmitSplit).not.toHaveBeenCalled();
  });
});
