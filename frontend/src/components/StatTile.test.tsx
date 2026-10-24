import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StatTile from './StatTile';

describe('StatTile', () => {
  it('renders label, value, and hint', () => {
    render(<StatTile label="Episodes" value="42" hint="last 7 days" />);
    expect(screen.getByText('Episodes')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
    expect(screen.getByText('last 7 days')).toBeTruthy();
  });

  it('colours only the value by tone', () => {
    render(<StatTile label="Failed" value="3" tone="destructive" />);
    expect(screen.getByText('3').classList.contains('text-destructive')).toBe(true);
    expect(screen.getByText('Failed').classList.contains('text-destructive')).toBe(false);
  });

  it('stretches to the grid row and accepts a className', () => {
    const { container } = render(<StatTile label="A" value="1" className="extra" />);
    const root = container.firstChild as HTMLElement;
    expect(root.classList.contains('h-full') && root.classList.contains('extra')).toBe(true);
  });
});
