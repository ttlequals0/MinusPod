import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
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

  it('spans three subgrid rows so values align across a grid row', () => {
    const { container } = render(<StatTile label="A" value="1" />);
    const cls = (container.firstChild as HTMLElement).classList;
    expect(cls.contains('row-span-3') && cls.contains('grid-rows-subgrid')).toBe(true);
  });

  it('renders a button that is the tile itself when onClick is given', () => {
    const onClick = vi.fn();
    render(<StatTile label="Failed" value="3" onClick={onClick} selected title="Filter" />);
    const btn = screen.getByRole('button', { name: /Failed/ });
    expect(btn.classList.contains('row-span-3') && btn.classList.contains('grid-rows-subgrid')).toBe(true);
    expect(btn.getAttribute('aria-pressed')).toBe('true');
    expect(btn.getAttribute('title')).toBe('Filter');
    expect(btn.classList.contains('border-primary')).toBe(true);
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(btn.querySelector('button')).toBeNull();
  });

  it('is not pressed by default and renders no button without onClick', () => {
    const { rerender } = render(<StatTile label="A" value="1" onClick={() => {}} />);
    expect(screen.getByRole('button').getAttribute('aria-pressed')).toBe('false');
    rerender(<StatTile label="A" value="1" />);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
