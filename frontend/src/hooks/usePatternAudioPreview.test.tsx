import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { usePatternAudioPreview } from './usePatternAudioPreview';

const mockFetchClip = vi.fn();

vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  fetchPatternAudioClip: (...a: unknown[]) => mockFetchClip(...a),
}));

function Harness() {
  const preview = usePatternAudioPreview(7);
  return (
    <>
      <button type="button" onClick={() => preview.toggle()}>source</button>
      <button type="button" onClick={() => preview.toggle(5)}>match</button>
      <output data-testid="state">
        {[preview.isPlaying() ? 'source-playing' : '', preview.isPlaying(5) ? 'match-playing' : '', preview.isLoading(5) ? 'match-loading' : '']
          .filter(Boolean).join(' ')}
      </output>
      <output data-testid="error-source">{preview.errorFor() ?? ''}</output>
      <output data-testid="error-match">{preview.errorFor(5) ?? ''}</output>
      {preview.audioElement}
    </>
  );
}

const busy = () => new ApiError('busy', 503, { error: 'busy' }, 2000, true);

let urlCount = 0;
let revoked: string[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  urlCount = 0;
  revoked = [];
  vi.stubGlobal('URL', Object.assign(URL, {
    createObjectURL: vi.fn(() => `blob:clip-${++urlCount}`),
    revokeObjectURL: vi.fn((u: string) => { revoked.push(u); }),
  }));
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('usePatternAudioPreview', () => {
  it('fetches the clip once and plays it from a blob url', async () => {
    mockFetchClip.mockResolvedValue(new Blob(['a']));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('match-playing'));
    expect(mockFetchClip).toHaveBeenCalledTimes(1);
    expect(mockFetchClip).toHaveBeenCalledWith(7, 5, expect.any(AbortSignal));
    expect(document.querySelector('audio')?.getAttribute('src')).toBe('blob:clip-1');
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
  });

  it('shows a loading state while the clip is prepared', async () => {
    let finish: (b: Blob) => void = () => {};
    mockFetchClip.mockReturnValue(new Promise<Blob>((resolve) => { finish = resolve; }));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    expect(screen.getByTestId('state').textContent).toBe('match-loading');
    await act(async () => { finish(new Blob(['a'])); });
    expect(screen.getByTestId('state').textContent).toBe('match-playing');
  });

  it('reports a busy server, then a second click makes a new request and plays', async () => {
    mockFetchClip.mockRejectedValueOnce(busy()).mockResolvedValueOnce(new Blob(['a']));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('error-match').textContent).toBe('Audio is being prepared. Try again.'));
    expect(screen.getByTestId('state').textContent).toBe('');
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('match-playing'));
    expect(mockFetchClip).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('error-match').textContent).toBe('');
  });

  it('explains an original that is no longer kept and a pattern without audio', async () => {
    mockFetchClip.mockRejectedValueOnce(new ApiError('gone', 404, { code: 'original_not_retained' }))
      .mockRejectedValueOnce(new ApiError('none', 404, { code: 'no_audio' }))
      .mockRejectedValueOnce(new ApiError('boom', 500, {}));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('error-match').textContent).toBe("This episode's original audio is no longer kept."));
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('error-match').textContent).toBe('This pattern has no audio fingerprint.'));
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('error-match').textContent).toBe('Could not play this audio. Try again.'));
  });

  it('reports a clip the element cannot decode', async () => {
    mockFetchClip.mockResolvedValue(new Blob(['a']));
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new Error('decode'));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'source' }));
    await waitFor(() => expect(screen.getByTestId('error-source').textContent).toBe('Could not play this audio. Try again.'));
    expect(screen.getByTestId('state').textContent).toBe('');
  });

  it('a second click on a playing clip stops it without another request', async () => {
    mockFetchClip.mockResolvedValue(new Blob(['a']));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('match-playing'));
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    expect(screen.getByTestId('state').textContent).toBe('');
    expect(mockFetchClip).toHaveBeenCalledTimes(1);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });

  it('revokes the previous object url when another clip loads and on unmount', async () => {
    mockFetchClip.mockResolvedValue(new Blob(['a']));
    const { unmount } = render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('match-playing'));
    await userEvent.click(screen.getByRole('button', { name: 'source' }));
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('source-playing'));
    expect(revoked).toEqual(['blob:clip-1']);
    unmount();
    expect(revoked).toEqual(['blob:clip-1', 'blob:clip-2']);
  });

  it('aborts a request still in flight on unmount', async () => {
    let signal: AbortSignal | undefined;
    mockFetchClip.mockImplementation((_id: number, _m: number, s: AbortSignal) => { signal = s; return new Promise(() => {}); });
    const { unmount } = render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
