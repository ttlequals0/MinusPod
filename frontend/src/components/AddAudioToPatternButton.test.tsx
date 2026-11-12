import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AddAudioToPatternButton from './AddAudioToPatternButton';

const mockGetPatterns = vi.fn();
const mockRebuild = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  getPatterns: (...a: unknown[]) => mockGetPatterns(...a),
  rebuildPatternAudio: (...a: unknown[]) => mockRebuild(...a),
}));

const SPAN = { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 120.4, end: 165.4 };
const summary = (state: string) => ({ state, available: state !== 'none', matchCount: 0, meanSimilarity: null });
const pattern = (id: number, state?: string) => ({ id, ...(state ? { audio: summary(state) } : {}) });

function renderButton(props: Partial<React.ComponentProps<typeof AddAudioToPatternButton>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  const { container } = render(
    <QueryClientProvider client={qc}>
      <AddAudioToPatternButton patternId={7} span={SPAN} hasOriginal {...props} />
    </QueryClientProvider>,
  );
  return { invalidate, container };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetPatterns.mockResolvedValue([pattern(7, 'none'), pattern(8, 'active')]);
});

describe('AddAudioToPatternButton', () => {
  it('offers the action for a pattern without audio and sends the span as shown', async () => {
    const { invalidate } = renderButton();
    mockRebuild.mockResolvedValue({ fingerprintStored: true, state: 'active' });
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    expect(mockRebuild).toHaveBeenCalledWith(7, SPAN);
    expect((await screen.findByRole('status')).textContent).toBe('Audio added to pattern.');
    expect(screen.queryByRole('button', { name: 'Add audio to pattern' })).toBeNull();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['patterns'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['pattern-audio', 7] });
  });

  it('renders nothing for a pattern that already has audio, on or off', async () => {
    mockGetPatterns.mockResolvedValue([pattern(7, 'active')]);
    const { container } = renderButton();
    await waitFor(() => expect(mockGetPatterns).toHaveBeenCalled());
    expect(container.textContent).toBe('');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders nothing while the summaries load', () => {
    mockGetPatterns.mockReturnValue(new Promise(() => {}));
    renderButton();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders nothing when the summaries fail to load', async () => {
    mockGetPatterns.mockRejectedValue(new Error('boom'));
    renderButton();
    await waitFor(() => expect(mockGetPatterns).toHaveBeenCalled());
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders nothing for a pattern that has no audio field', async () => {
    mockGetPatterns.mockResolvedValue([pattern(7)]);
    renderButton();
    await waitFor(() => expect(mockGetPatterns).toHaveBeenCalled());
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('is disabled with a visible reason when the original is not kept', async () => {
    renderButton({ hasOriginal: false });
    const button = await screen.findByRole('button', { name: 'Add audio to pattern' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText('Source audio expired')).toBeDefined();
    await userEvent.click(button);
    expect(mockRebuild).not.toHaveBeenCalled();
  });

  it('shows the reason when no fingerprint was stored and keeps the action', async () => {
    mockRebuild.mockResolvedValue({ fingerprintStored: false, fingerprintSkippedReason: 'segment_too_short' });
    renderButton();
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    expect((await screen.findByRole('alert')).textContent).toBe('That selection is too short for an audio fingerprint.');
    expect(screen.getByRole('button', { name: 'Add audio to pattern' })).toBeDefined();
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('shows the server message when the request fails', async () => {
    mockRebuild.mockRejectedValue(new Error('Episode not found'));
    renderButton();
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Episode not found');
  });

  it('names the pending operation and blocks a second submission', async () => {
    let finish: (v: unknown) => void = () => {};
    mockRebuild.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    renderButton();
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    const busy = await screen.findByRole('button', { name: 'Adding audio...' }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    finish({ fingerprintStored: true });
    expect((await screen.findByRole('status')).textContent).toBe('Audio added to pattern.');
    expect(mockRebuild).toHaveBeenCalledTimes(1);
  });

  it('uses the row state without fetching the pattern list', async () => {
    mockRebuild.mockResolvedValue({ fingerprintStored: true, state: 'active' });
    const { container } = renderButton({ audioState: 'none' });
    await userEvent.click(await screen.findByRole('button', { name: 'Add audio to pattern' }));
    expect(mockGetPatterns).not.toHaveBeenCalled();
    expect(container.textContent).toContain('Audio added to pattern.');
  });

  it.each(['active', 'off'] as const)('renders nothing when the row says the pattern has audio %s', (audioState) => {
    const { container } = renderButton({ audioState });
    expect(container.textContent).toBe('');
    expect(mockGetPatterns).not.toHaveBeenCalled();
  });

  it('announces success through a live region that was mounted before the text and takes focus', async () => {
    mockRebuild.mockResolvedValue({ fingerprintStored: true, state: 'active' });
    renderButton({ audioState: 'none' });
    const region = screen.getByRole('status');
    expect(region.textContent).toBe('');
    await userEvent.click(screen.getByRole('button', { name: 'Add audio to pattern' }));
    await waitFor(() => expect(region.textContent).toBe('Audio added to pattern.'));
    expect(document.activeElement).toBe(region);
  });
});
