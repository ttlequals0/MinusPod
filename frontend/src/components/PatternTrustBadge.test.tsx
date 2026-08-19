import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PatternTrustBadge } from './PatternTrustBadge';

describe('PatternTrustBadge', () => {
  it('renders nothing for active', () => {
    const { container } = render(<PatternTrustBadge trust="active" />);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when trust is absent', () => {
    const { container } = render(<PatternTrustBadge />);
    expect(container.firstChild).toBeNull();
  });

  it('renders a warning-toned Stale badge', () => {
    render(<PatternTrustBadge trust="stale" />);
    const badge = screen.getByText('Stale');
    expect(badge.className).toContain('text-warning');
  });

  it('renders a muted Unproven badge', () => {
    render(<PatternTrustBadge trust="unproven" />);
    const badge = screen.getByText('Unproven');
    expect(badge.className).toContain('text-muted-foreground');
  });
});
