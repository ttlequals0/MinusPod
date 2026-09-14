import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import CostAmount, { IncompleteBadge } from './CostAmount';

describe('CostAmount', () => {
  it('renders a plain amount when nothing is unpriced', () => {
    render(<CostAmount amount={1.2345} />);
    expect(screen.getByText('$1.2345')).toBeTruthy();
    expect(screen.queryByText('Incomplete')).toBeNull();
  });

  it('labels a partly unpriced amount as a known floor', () => {
    render(<CostAmount amount={0.9} unpricedCount={4} />);
    expect(screen.getByText(/Known \$0\.9000/)).toBeTruthy();
    expect(screen.getByText('Incomplete').getAttribute('title')).toMatch(/4 unpriced calls/);
  });

  it('says the breakdown is unavailable when the known amount is zero', () => {
    render(<CostAmount amount={0} unpricedCount={2} />);
    expect(screen.getByText('Breakdown unavailable')).toBeTruthy();
    expect(screen.queryByText('Incomplete')).toBeNull();
  });

  it('takes the unpriced flag without a count', () => {
    render(<CostAmount amount={0.05} unpriced />);
    expect(screen.getByText(/Known \$0\.0500/)).toBeTruthy();
    expect(screen.getByText('Incomplete').getAttribute('title')).not.toMatch(/unpriced/);
  });

  it('names the unpriced rows with the caller unit, singularized', () => {
    render(<IncompleteBadge unpricedCount={1} unit="phase group" />);
    expect(screen.getByText('Incomplete').getAttribute('title')).toMatch(/1 unpriced phase group\)/);
  });
});
