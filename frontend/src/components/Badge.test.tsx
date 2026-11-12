import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Badge } from './Badge';
import { badgeBase, tint } from './badgeStyles';

describe('Badge', () => {
  it('stays inside its container and truncates the text in an inner span', () => {
    render(<Badge tone="teal" title="A very long name">A very long name</Badge>);
    const badge = screen.getByTitle('A very long name');
    expect(badge.tagName).toBe('SPAN');
    expect(badge.className).toContain(badgeBase);
    expect(badge.className).toContain(tint.teal);
    expect(badge.className.split(/\s+/)).toContain('max-w-full');
    expect(badge.firstElementChild?.className).toBe('truncate');
  });

  it('lets the text wrap, with no truncating span, when asked', () => {
    render(<Badge tone="blue" title="Wraps" wrap>Wraps</Badge>);
    const badge = screen.getByTitle('Wraps');
    expect(badge.className).toContain('whitespace-normal!');
    expect(badge.querySelector('.truncate')).toBeNull();
  });

  it('renders a button with the same box when it has a click handler', () => {
    const onClick = vi.fn();
    render(<Badge tone="warning" onClick={onClick} className="extra">Press</Badge>);
    const button = screen.getByRole('button', { name: 'Press' });
    expect(button.getAttribute('type')).toBe('button');
    expect(button.className).toContain('extra');
    expect(button.className.split(/\s+/)).toContain('max-w-full');
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
