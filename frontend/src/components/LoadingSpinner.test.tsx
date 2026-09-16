import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import LoadingSpinner from './LoadingSpinner';

describe('LoadingSpinner', () => {
  it('renders phrasing content inline, so it can sit inside a paragraph', () => {
    const { container } = render(
      <p>
        <LoadingSpinner size="sm" inline className="w-4 h-4" /> Scanning
      </p>,
    );
    expect(container.querySelector('p div')).toBeNull();
    expect(container.querySelector('p span.animate-spin')).toBeTruthy();
  });

  it('keeps the centered block layout when not inline', () => {
    const { container } = render(<LoadingSpinner size="md" />);
    expect(container.querySelector('div > div.animate-spin')).toBeTruthy();
  });
});
