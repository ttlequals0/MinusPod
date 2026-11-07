import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import EpisodePlayer from './EpisodePlayer';
import { DEMO_AUDIO_TOOLTIP } from '../demo/copy';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));

afterEach(cleanup);

describe('EpisodePlayer in demo', () => {
  it('wraps the disabled play button in the audio tooltip when src is null', () => {
    render(<EpisodePlayer src={null} />);
    const play = screen.getByRole('button', { name: /^play$/i }) as HTMLButtonElement;
    expect(play.disabled).toBe(true);
    expect(play.parentElement?.getAttribute('title')).toBe(DEMO_AUDIO_TOOLTIP);
  });
});
