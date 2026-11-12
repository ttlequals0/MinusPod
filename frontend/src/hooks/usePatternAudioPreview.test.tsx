import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { patternAudioPreviewUrl } from '../api/patterns';
import { usePatternAudioPreview } from './usePatternAudioPreview';

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

const matchUrl = patternAudioPreviewUrl(7, 5) as string;
const audioEl = () => document.querySelector('audio') as HTMLAudioElement;
const state = () => screen.getByTestId('state').textContent;
const errorText = () => screen.getByTestId('error-match').textContent;

function reply(status: number, body?: object, headers: Record<string, string> = {}) {
  return new Response(body ? JSON.stringify(body) : null, { status, headers });
}

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchSpy = vi.fn();
  vi.stubGlobal('fetch', fetchSpy);
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('usePatternAudioPreview', () => {
  it('assigns the source and calls play inside the click, before any request resolves', () => {
    fetchSpy.mockReturnValue(new Promise(() => {}));
    const order: string[] = [];
    vi.mocked(HTMLMediaElement.prototype.play).mockImplementation(function play(this: HTMLMediaElement) {
      order.push(`play:${this.getAttribute('src')}`);
      return Promise.resolve();
    });
    render(<Harness />);
    act(() => { screen.getByRole('button', { name: 'match' }).click(); });
    expect(order).toEqual([`play:${matchUrl}`]);
    expect(state()).toBe('match-loading');
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('follows the element events from loading to playing to ended', () => {
    render(<Harness />);
    act(() => { screen.getByRole('button', { name: 'match' }).click(); });
    fireEvent.waiting(audioEl());
    expect(state()).toBe('match-loading');
    fireEvent.playing(audioEl());
    expect(state()).toBe('match-playing');
    fireEvent.waiting(audioEl());
    expect(state()).toBe('match-loading');
    fireEvent.playing(audioEl());
    fireEvent.ended(audioEl());
    expect(state()).toBe('');
  });

  it('a second click on a playing clip stops it', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    fireEvent.playing(audioEl());
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    expect(state()).toBe('');
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
  });

  it('after an error the next click reassigns src and plays again', async () => {
    fetchSpy.mockResolvedValue(reply(503, { error: 'busy' }, { 'Retry-After': '2' }));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    fireEvent.error(audioEl());
    await waitFor(() => expect(errorText()).toBe('Audio is being prepared. Try again.'));
    expect(state()).toBe('');
    expect(audioEl().hasAttribute('src')).toBe(false);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe(matchUrl);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    expect(audioEl().getAttribute('src')).toBe(matchUrl);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
    expect(errorText()).toBe('');
    fireEvent.playing(audioEl());
    expect(state()).toBe('match-playing');
  });

  it('explains each failure from the status and code of one follow-up request', async () => {
    fetchSpy
      .mockResolvedValueOnce(reply(404, { code: 'original_not_retained' }))
      .mockResolvedValueOnce(reply(404, { code: 'no_audio' }))
      .mockResolvedValueOnce(reply(503, { error: 'busy' }))
      .mockResolvedValueOnce(reply(500, {}));
    render(<Harness />);
    const expected = [
      "This episode's original audio is no longer kept.",
      'This pattern has no audio fingerprint.',
      'Could not play this audio. Try again.',
      'Could not play this audio. Try again.',
    ];
    for (const text of expected) {
      await userEvent.click(screen.getByRole('button', { name: 'match' }));
      fireEvent.error(audioEl());
      await waitFor(() => expect(errorText()).toBe(text));
    }
    expect(fetchSpy).toHaveBeenCalledTimes(4);
  });

  it('tells the user to tap again when the browser blocks play', async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new DOMException('blocked', 'NotAllowedError'));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    await waitFor(() => expect(errorText()).toBe('Tap play again to start the audio.'));
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(state()).toBe('');
  });

  it('explains another rejected play() with the follow-up request', async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new DOMException('bad', 'NotSupportedError'));
    fetchSpy.mockResolvedValue(reply(404, { code: 'no_audio' }));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'source' }));
    await waitFor(() => expect(screen.getByTestId('error-source').textContent).toBe('This pattern has no audio fingerprint.'));
  });

  it('makes one follow-up request when both play() and the element report the failure', async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new DOMException('bad', 'NotSupportedError'));
    fetchSpy.mockResolvedValue(reply(500, {}));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    fireEvent.error(audioEl());
    await waitFor(() => expect(errorText()).toBe('Could not play this audio. Try again.'));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('aborts the follow-up request on unmount', async () => {
    let signal: AbortSignal | undefined;
    fetchSpy.mockImplementation((_url: string, init: RequestInit) => { signal = init.signal as AbortSignal; return new Promise(() => {}); });
    const { unmount } = render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    fireEvent.error(audioEl());
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it('ignores the rejected play of the first row when a second row was tapped right after', async () => {
    let rejectFirst: (e: unknown) => void = () => {};
    vi.mocked(HTMLMediaElement.prototype.play)
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectFirst = reject; }))
      .mockResolvedValueOnce(undefined);
    render(<Harness />);
    act(() => { screen.getByRole('button', { name: 'source' }).click(); });
    act(() => { screen.getByRole('button', { name: 'match' }).click(); });
    await act(async () => { rejectFirst(new DOMException('interrupted', 'NotSupportedError')); });
    expect(screen.getByTestId('error-source').textContent).toBe('');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(audioEl().getAttribute('src')).toBe(matchUrl);
    fireEvent.playing(audioEl());
    expect(state()).toBe('match-playing');
  });

  it('leaves playing when the element pauses on its own', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    fireEvent.playing(audioEl());
    expect(state()).toBe('match-playing');
    fireEvent.pause(audioEl());
    expect(state()).toBe('');
  });

  it('empties the element source on unmount', async () => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
    const { unmount } = render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'match' }));
    const el = audioEl();
    unmount();
    expect(el.hasAttribute('src')).toBe(false);
    expect(load).toHaveBeenCalled();
  });
});
