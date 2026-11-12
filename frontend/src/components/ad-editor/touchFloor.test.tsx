import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { touchTarget, touchTargetRow } from '../buttonStyles';
import SpeedMenu from './SpeedMenu';
import ZoomControl from './ZoomControl';

// A min-w-0 from sm up beats the coarse-pointer base rule in index.css and shrinks the target.
const resetsWidthFromSm = (cls: string) => cls.split(/\s+/).some((c) => /^(sm|md|lg):min-w-0$/.test(c));

describe('coarse-pointer width floor', () => {
  it('touchTarget and touchTargetRow leave min-width to the base rule from sm up', () => {
    expect(resetsWidthFromSm(touchTarget)).toBe(false);
    expect(resetsWidthFromSm(touchTargetRow)).toBe(false);
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
});
