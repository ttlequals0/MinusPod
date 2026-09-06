import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderSnippet } from './searchSnippet';

describe('renderSnippet', () => {
  it('decodes escaped characters in plain text instead of showing the entity', () => {
    render(<div data-testid="out">{renderSnippet('sponsored by AT&amp;T today')}</div>);
    expect(screen.getByTestId('out').textContent).toBe('sponsored by AT&T today');
  });

  it('decodes escaped characters inside a highlight', () => {
    render(<div data-testid="out">{renderSnippet('<mark>AT&amp;T</mark> ads')}</div>);
    expect(screen.getByTestId('out').querySelector('mark')?.textContent).toBe('AT&T');
  });

  it('shows a literal mark tag as text, not as a highlight', () => {
    render(<div data-testid="out">{renderSnippet('see &lt;mark&gt; in <mark>notes</mark>')}</div>);
    const out = screen.getByTestId('out');
    expect(out.textContent).toBe('see <mark> in notes');
    expect(out.querySelectorAll('mark')).toHaveLength(1);
  });
});
