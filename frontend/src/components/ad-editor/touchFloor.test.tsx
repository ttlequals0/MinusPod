import { readFileSync } from 'node:fs';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { touchTarget, touchTargetRow } from '../buttonStyles';
import { edgeBtn } from './controlStyles';
import SpeedMenu from './SpeedMenu';
import TransportBar from './TransportBar';
import ZoomControl from './ZoomControl';

// A min-w-0 from sm up beats the coarse-pointer base rule in index.css and shrinks the target.
const resetsWidthFromSm = (cls: string) => cls.split(/\s+/).some((c) => /^(sm|md|lg):min-w-0$/.test(c));

describe('coarse-pointer width floor', () => {
  it('touchTarget keeps the explicit floor below sm only', () => {
    const classes = touchTarget.split(/\s+/);
    expect(classes).toContain('max-sm:min-w-11');
    expect(classes).not.toContain('sm:min-w-0');
  });

  it('touchTarget and touchTargetRow leave min-width to the base rule from sm up', () => {
    expect(resetsWidthFromSm(touchTarget)).toBe(false);
    expect(resetsWidthFromSm(touchTargetRow)).toBe(false);
  });

  it('index.css gives every touchTarget element a coarse-pointer width default in the base layer', () => {
    const css = readFileSync('src/index.css', 'utf8');
    expect(css).toMatch(/@layer base \{\s*@media \(pointer: coarse\)\s*\{[^}]*\.touch-target[^}]*min-width: 44px/);
    expect(touchTarget.split(/\s+/)).toContain('touch-target');
  });

  it('the zoom buttons use the shared recipe', () => {
    render(<ZoomControl value={2} min={1} max={10} onChange={() => {}} onZoomIn={() => {}} onZoomOut={() => {}} />);
    for (const name of ['Zoom in', 'Zoom out']) {
      const cls = screen.getByRole('button', { name }).className;
      expect(cls).toContain('touch-target');
      expect(resetsWidthFromSm(cls)).toBe(false);
    }
  });

  it('the speed trigger shares the row width below sm and keeps the base floor from sm', () => {
    render(<SpeedMenu playbackRate={1} onChange={() => {}} />);
    const cls = screen.getByRole('button', { name: 'Playback speed' }).className;
    expect(cls).toContain('max-sm:min-w-0');
    expect(resetsWidthFromSm(cls)).toBe(false);
  });

  it('the Play button reserves its border below sm only, so it keeps its size on desktop', () => {
    render(
      <TransportBar isPlaying={false} onTogglePlay={() => {}} onSeekToStart={() => {}} onSeekToEnd={() => {}}
        onSeekRelative={() => {}} onStop={() => {}} playbackRate={1} onPlaybackRateChange={() => {}}
        currentTime={0} selectionDuration={10} inSelection={false} />,
    );
    const classes = screen.getByRole('button', { name: /Play \/ pause/ }).className.split(/\s+/);
    expect(classes).toContain('max-sm:border');
    expect(classes).not.toContain('border');
    expect(classes).not.toContain('border-transparent');
  });

  it('the speed trigger keeps one width below sm whatever the rate, so the row does not reflow', () => {
    for (const rate of [0.5, 1, 1.75]) {
      const { unmount } = render(<SpeedMenu playbackRate={rate} onChange={() => {}} />);
      const trigger = screen.getByRole('button', { name: 'Playback speed' });
      expect(trigger.className).toContain('max-sm:min-w-14');
      expect(trigger.querySelector('span')?.className).toContain('min-w-[5ch]');
      unmount();
    }
  });

  it('the transport bar spans the dialog body edge to edge below sm', () => {
    const { container } = render(
      <TransportBar isPlaying={false} onTogglePlay={() => {}} onSeekToStart={() => {}} onSeekToEnd={() => {}}
        onSeekRelative={() => {}} onStop={() => {}} playbackRate={1} onPlaybackRateChange={() => {}}
        currentTime={0} selectionDuration={10} inSelection={false} />,
    );
    const classes = (container.firstElementChild as HTMLElement).className.split(/\s+/);
    expect(classes.filter((c) => /-mx-|^max-sm:w-auto$|^max-sm:max-w-none$/.test(c))).toEqual([]);
  });

  it('the selection readout stays on one line and wraps below the controls when the bar is too narrow', () => {
    render(
      <TransportBar isPlaying={false} onTogglePlay={() => {}} onSeekToStart={() => {}} onSeekToEnd={() => {}}
        onSeekRelative={() => {}} onStop={() => {}} playbackRate={1} onPlaybackRateChange={() => {}}
        currentTime={0} selectionDuration={90} inSelection={false} />,
    );
    const readout = screen.getByText('1:30.0 selection').parentElement as HTMLElement;
    expect(readout.className).toContain('sm:flex-nowrap');
    expect(readout.className).toContain('sm:whitespace-nowrap');
    expect(readout.parentElement?.className).toContain('sm:flex-wrap');
  });

  it('the speed trigger drops to its own row under 336 px of bar width, below sm only', () => {
    const { container } = render(
      <TransportBar isPlaying={false} onTogglePlay={() => {}} onSeekToStart={() => {}} onSeekToEnd={() => {}}
        onSeekRelative={() => {}} onStop={() => {}} playbackRate={1} onPlaybackRateChange={() => {}}
        currentTime={0} selectionDuration={10} inSelection={false} />,
    );
    expect((container.firstElementChild as HTMLElement).className).toContain('max-sm:[container-type:inline-size]');
    const brk = container.querySelector('[aria-hidden="true"].basis-full') as HTMLElement;
    expect(brk.className).toContain('max-sm:@max-[21rem]:block');
    expect(brk.className.split(/\s+/)).toContain('hidden');
    expect(brk.parentElement?.className).toContain('max-sm:flex-wrap');
  });

  it('the Set START and END buttons carry the recipe padding', () => {
    const classes = edgeBtn.split(/\s+/);
    expect(classes).toContain('px-3');
    expect(classes).not.toContain('px-2');
  });
});
