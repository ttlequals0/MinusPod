import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import EpisodePlayer from './EpisodePlayer';
import { RATE_STORAGE_KEY } from '../hooks/useEpisodePlayer';

const CHAPTERS = {
  version: '1.2.0',
  chapters: [
    { startTime: 0, title: 'Intro' },
    { startTime: 100, title: 'Middle part' },
    { startTime: 250, title: 'Wrap up' },
  ],
};

let paused = true;
let play: ReturnType<typeof vi.spyOn>;
let pause: ReturnType<typeof vi.spyOn>;

function audioEl(container: HTMLElement) {
  const audio = container.querySelector('audio')!;
  let t = 0;
  Object.defineProperty(audio, 'currentTime', { configurable: true, get: () => t, set: (v: number) => { t = v; } });
  Object.defineProperty(audio, 'duration', { configurable: true, get: () => 300 });
  Object.defineProperty(audio, 'paused', { configurable: true, get: () => paused });
  return audio;
}

function ready(container: HTMLElement, at = 0) {
  const audio = audioEl(container);
  audio.currentTime = at;
  fireEvent.durationChange(audio);
  fireEvent.timeUpdate(audio);
  return audio;
}

beforeEach(() => {
  paused = true;
  localStorage.clear();
  play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    paused = false;
    fireEvent.play(this);
    return Promise.resolve();
  });
  pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    paused = true;
    fireEvent.pause(this);
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(CHAPTERS) }));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('EpisodePlayer', () => {
  it('hides the chapter row without a chapters URL', () => {
    render(<EpisodePlayer src="/a.mp3" />);
    expect(screen.queryByRole('button', { name: 'Previous chapter' })).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows the current chapter title and clock', async () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" chaptersUrl="/c.json" />);
    await screen.findByText('Intro');
    ready(container, 125);
    expect(screen.getByText('Middle part')).toBeTruthy();
    expect(screen.getByText('2:05')).toBeTruthy();
    expect(screen.getByText('-2:55')).toBeTruthy();
  });

  it('toggles play and pause', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    ready(container);
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(play).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(pause).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
  });

  it('skips back 15 s and forward 30 s with clamping', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    const audio = ready(container, 10);
    fireEvent.click(screen.getByRole('button', { name: 'Back 15 seconds' }));
    expect(audio.currentTime).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: 'Forward 30 seconds' }));
    expect(audio.currentTime).toBe(30);
    audio.currentTime = 290;
    fireEvent.click(screen.getByRole('button', { name: 'Forward 30 seconds' }));
    expect(audio.currentTime).toBe(300);
  });

  it('cycles speed and stores it', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    const audio = ready(container);
    const speed = () => screen.getByRole('button', { name: /Playback speed/ });
    expect(speed().textContent).toContain('1x');
    fireEvent.click(speed());
    expect(speed().textContent).toContain('1.25x');
    expect(audio.playbackRate).toBe(1.25);
    expect(localStorage.getItem(RATE_STORAGE_KEY)).toBe('1.25');
    for (let i = 0; i < 4; i++) fireEvent.click(speed());
    expect(speed().textContent).toContain('0.8x');
    fireEvent.click(speed());
    expect(speed().textContent).toContain('1x');
  });

  it('starts at the stored speed', () => {
    localStorage.setItem(RATE_STORAGE_KEY, '2');
    render(<EpisodePlayer src="/a.mp3" />);
    expect(screen.getByRole('button', { name: 'Playback speed 2x' })).toBeTruthy();
  });

  it('shows no chapter title before the first chapter and offers only next', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ chapters: [{ startTime: 10, title: 'First' }] }) }));
    const { container } = render(<EpisodePlayer src="/a.mp3" chaptersUrl="/c.json" />);
    const next = await screen.findByRole('button', { name: 'Next chapter' });
    ready(container, 2);
    expect(screen.queryByText('First')).toBeNull();
    expect((next as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: 'Previous chapter' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('warns once when the chapters response is not JSON', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.reject(new Error('bad')) }));
    render(<EpisodePlayer src="/a.mp3" chaptersUrl="/c.json" />);
    await waitFor(() => expect(warn).toHaveBeenCalledWith('chapters response was not JSON'));
    expect(screen.queryByRole('button', { name: 'Next chapter' })).toBeNull();
  });

  it('falls back to 1x when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    render(<EpisodePlayer src="/a.mp3" />);
    expect(screen.getByRole('button', { name: 'Playback speed 1x' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Playback speed/ }));
    expect(screen.getByRole('button', { name: 'Playback speed 1.25x' })).toBeTruthy();
  });

  it('navigates chapters with the 3 s rule and disables at the ends', async () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" chaptersUrl="/c.json" />);
    await screen.findByText('Intro');
    const audio = ready(container, 1);
    const prev = screen.getByRole('button', { name: 'Previous chapter' }) as HTMLButtonElement;
    const next = screen.getByRole('button', { name: 'Next chapter' }) as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    fireEvent.click(next);
    expect(audio.currentTime).toBe(100);
    fireEvent.timeUpdate(audio);
    audio.currentTime = 110;
    fireEvent.timeUpdate(audio);
    fireEvent.click(prev);
    expect(audio.currentTime).toBe(100);
    fireEvent.timeUpdate(audio);
    fireEvent.click(prev);
    expect(audio.currentTime).toBe(0);
    audio.currentTime = 260;
    fireEvent.timeUpdate(audio);
    expect(next.disabled).toBe(true);
  });

  it('seeks from the scrubber', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    const audio = ready(container);
    const slider = screen.getByRole('slider', { name: 'Seek' });
    fireEvent.change(slider, { target: { value: '120' } });
    expect(audio.currentTime).toBe(0);
    fireEvent.pointerUp(slider);
    expect(audio.currentTime).toBe(120);
    fireEvent.change(slider, { target: { value: '200' } });
    fireEvent.keyUp(slider, { key: 'ArrowRight' });
    expect(audio.currentTime).toBe(200);
    fireEvent.change(slider, { target: { value: '10' } });
    fireEvent.pointerCancel(slider);
    expect(audio.currentTime).toBe(200);
  });

  it('handles keyboard shortcuts on the player and leaves buttons alone', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    const audio = ready(container, 60);
    const group = screen.getByRole('group', { name: 'Episode player' });
    fireEvent.keyDown(group, { key: 'ArrowRight' });
    expect(audio.currentTime).toBe(90);
    fireEvent.keyDown(group, { key: 'ArrowLeft' });
    expect(audio.currentTime).toBe(75);
    fireEvent.keyDown(group, { key: ' ' });
    expect(play).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('button', { name: 'Back 15 seconds' }), { key: ' ' });
    expect(play).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('slider', { name: 'Seek' }), { key: 'ArrowRight' });
    expect(audio.currentTime).toBe(75);
    fireEvent.keyDown(group, { key: 'ArrowRight', ctrlKey: true });
    expect(audio.currentTime).toBe(75);
  });

  it('keeps playing without chapters when the fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
    render(<EpisodePlayer src="/a.mp3" chaptersUrl="/c.json" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Next chapter' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
    expect(await screen.findByText('Could not load chapters.')).toBeTruthy();
  });

  it('reports failed playback and retries the audio resource', async () => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    const audio = ready(container);
    fireEvent.error(audio);
    expect(screen.getByRole('alert').textContent).toContain('Could not play this audio.');
    expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Retry playback' }));
    expect(load).toHaveBeenCalledOnce();
    expect(play).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('reports a rejected play request without leaving the player playing', async () => {
    play.mockRejectedValue(new Error('down'));
    render(<EpisodePlayer src="/a.mp3" />);
    fireEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(await screen.findByText('Could not play this audio.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play' })).toBeTruthy();
  });

  it('reports buffering and clears it when playback resumes', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    const audio = ready(container);
    fireEvent.waiting(audio);
    expect(screen.getByRole('status').textContent).toBe('Loading audio...');
    fireEvent.playing(audio);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('does not seek before metadata and resets on a replacement resource', () => {
    const { container } = render(<EpisodePlayer src="/a.mp3" />);
    expect((screen.getByRole('button', { name: 'Back 15 seconds' }) as HTMLButtonElement).disabled).toBe(true);
    const audio = ready(container, 60);
    fireEvent.emptied(audio);
    expect(screen.getByText('0:00')).toBeTruthy();
    expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
  });

  it('retries chapter failures without replacing the audio player', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue({ ok: true, json: () => Promise.resolve(CHAPTERS) }));
    const { container } = render(<EpisodePlayer src="/a.mp3" chaptersUrl="/c.json" />);
    const audio = ready(container, 125);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry chapters' }));
    expect(await screen.findByText('Middle part')).toBeTruthy();
    expect(audio.currentTime).toBe(125);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
