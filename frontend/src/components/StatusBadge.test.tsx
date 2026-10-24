import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StatusBadge from './StatusBadge';

describe('StatusBadge', () => {
  it('renders the status label with the shared badge shape', () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText('Completed').className).toContain('rounded');
  });

  it('passes className and title through', () => {
    render(<StatusBadge status="failed" className="cursor-help" title="boom" />);
    const badge = screen.getByText('Failed');
    expect(badge.className).toContain('cursor-help');
    expect(badge.getAttribute('title')).toBe('boom');
  });
});
