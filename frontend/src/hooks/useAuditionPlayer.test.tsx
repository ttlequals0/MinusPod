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
});
