/**
 * A marker reason names its pattern in more than one shape, and every shape
 * has to stay clickable.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import PatternLink from './PatternLink';

function renderReason(reason: string) {
  return render(
    <MemoryRouter>
      <PatternLink reason={reason} />
    </MemoryRouter>,
  );
}

describe('PatternLink', () => {
  it('links a bare pattern reference', () => {
    renderReason('Acme (pattern #12)');
    expect(screen.getByRole('link').getAttribute('href')).toBe('/patterns?id=12');
  });

  it('links a reference followed by the matched text', () => {
    renderReason('Acme (pattern #12, outro "for a free trial" 86%)');
    const link = screen.getByRole('link');
    expect(link.getAttribute('href')).toBe('/patterns?id=12');
    expect(link.textContent).toBe('pattern #12');
  });

  it('links the sponsorless shape', () => {
    renderReason('Pattern #485 (outro "brought to you by" 86%)');
    expect(screen.getByRole('link').getAttribute('href')).toBe('/patterns?id=485');
  });

  it('keeps the surrounding text', () => {
    const { container } = renderReason('Acme (pattern #12, outro "x" 86%)');
    expect(container.textContent).toBe('Acme (pattern #12, outro "x" 86%)');
  });

  it('links every reference in a merged reason', () => {
    renderReason('Acme (pattern #12) (merged with: Other (pattern #34))');
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it('renders plain text when there is no pattern reference', () => {
    const { container } = renderReason('Detected by audio cue');
    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).toBe('Detected by audio cue');
  });
});
