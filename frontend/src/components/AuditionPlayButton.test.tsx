import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AuditionPlayButton } from './AuditionPlayButton';

describe('AuditionPlayButton', () => {
  it('labels play and pause states and fires onClick', () => {
    const onClick = vi.fn();
    const { rerender } = render(<AuditionPlayButton playing={false} onClick={onClick} label="this match" />);
    fireEvent.click(screen.getByRole('button', { name: 'Play this match' }));
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(<AuditionPlayButton playing onClick={onClick} label="this match" />);
    expect(screen.getByRole('button', { name: 'Pause this match' })).toBeTruthy();
  });

  it('keeps the 44px target on the button and the fill on an inner span', () => {
    render(<AuditionPlayButton playing={false} onClick={() => {}} />);
    const button = screen.getByRole('button', { name: 'Play this ad' });
    expect(button.className).toContain('w-11');
    expect(button.className).toContain('min-h-11');
    expect(button.className).toContain('sm:h-7');
    expect(button.className).not.toContain('bg-primary');
    const visual = button.firstElementChild as HTMLElement;
    expect(visual.className).toContain('h-11 w-11');
    expect(visual.className).toContain('sm:h-7 sm:w-7');
    expect(visual.className).toContain('bg-primary');
  });

  it('nudges the Play glyph but not the Pause glyph', () => {
    const { container, rerender } = render(<AuditionPlayButton playing={false} onClick={() => {}} />);
    expect(container.querySelector('svg')!.getAttribute('class')).toContain('translate-x-px');
    rerender(<AuditionPlayButton playing onClick={() => {}} />);
    expect(container.querySelector('svg')!.getAttribute('class')).not.toContain('translate-x-px');
  });
});
