import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useAuditionPlayer } from './useAuditionPlayer';

function Harness({ showAudio }: { showAudio: boolean }) {
  const player = useAuditionPlayer();
  return (
    <div>
      <button type="button" onClick={() => player.toggle('a', '/original.mp3', 1, 5)}>play</button>
      {showAudio && player.audioElement}
    </div>
  );
}

function FailHarness() {
  const player = useAuditionPlayer();
  return (
    <div>
      <button type="button" onClick={() => player.toggle('a', '/original.mp3', 0, 5)}>play</button>
      <output data-testid="failed">{player.failedKey ?? ''}</output>
      {player.audioElement}
    </div>
  );
}

describe('useAuditionPlayer', () => {
  afterEach(() => vi.restoreAllMocks());

  it('pauses on unmount when the audio element mounted after the first render', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    const view = render(<Harness showAudio={false} />);
    view.rerender(<Harness showAudio />);
    fireEvent.click(screen.getByRole('button', { name: 'play' }));
    pause.mockClear();
    view.unmount();
    expect(pause).toHaveBeenCalled();
  });

  it('reports the key whose playback failed, and clears it on the next play', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValueOnce(new Error('no'));
    render(<FailHarness />);
    fireEvent.click(screen.getByRole('button', { name: 'play' }));
    await vi.waitFor(() => expect(screen.getByTestId('failed').textContent).toBe('a'));
    play.mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole('button', { name: 'play' }));
    expect(screen.getByTestId('failed').textContent).toBe('');
  });

  it('reports the playing key when the audio element errors', () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const view = render(<FailHarness />);
    fireEvent.click(screen.getByRole('button', { name: 'play' }));
    fireEvent.error(view.container.querySelector('audio') as HTMLAudioElement);
    expect(screen.getByTestId('failed').textContent).toBe('a');
  });
});
