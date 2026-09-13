import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SkeletonStatCards, SkeletonChart, SkeletonRows, SkeletonPageHeader } from './Skeleton';

describe('Skeleton', () => {
  it('renders the requested number of stat cards in the caller grid classes', () => {
    render(<SkeletonStatCards count={6} className="grid grid-cols-6" />);
    const grid = screen.getByTestId('skeleton-stat-cards');
    expect(grid.className).toContain('grid-cols-6');
    expect(grid.children).toHaveLength(6);
  });

  it('chart placeholder reserves the plot height so the page does not jump', () => {
    render(<SkeletonChart height={300} />);
    const plot = screen.getByTestId('skeleton-chart').lastElementChild as HTMLElement;
    expect(plot.style.height).toBe('300px');
  });

  it('inner blocks are hidden from assistive tech and pulse', () => {
    render(<SkeletonRows count={2} />);
    const blocks = screen.getByTestId('skeleton-rows').querySelectorAll('[aria-hidden="true"]');
    expect(blocks.length).toBeGreaterThan(0);
    for (const b of blocks) expect(b.className).toContain('animate-pulse');
  });

  it('stat cards reserve a third line when the card renders one', () => {
    render(<SkeletonStatCards count={1} lines={3} />);
    const card = screen.getByTestId('skeleton-stat-cards').firstElementChild as HTMLElement;
    expect(card.children).toHaveLength(3);
  });

  it('page header reserves a title and a control slot', () => {
    render(<SkeletonPageHeader />);
    expect(screen.getByTestId('skeleton-page-header').children).toHaveLength(2);
  });

  it('containers announce a loading state while inner blocks stay hidden', () => {
    render(<SkeletonStatCards count={2} />);
    const grid = screen.getByRole('status', { name: 'Loading' });
    expect(grid.getAttribute('aria-busy')).toBe('true');
    expect(grid.querySelectorAll('[aria-hidden="true"]').length).toBe(4);
  });
});
