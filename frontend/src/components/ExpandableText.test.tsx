/**
 * ExpandableText shows its control only when the content actually overflows,
 * so short entries stay a plain paragraph (issue #591).
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ExpandableText from './ExpandableText';

/** happy-dom reports 0 for both heights, so overflow has to be faked. */
function mockOverflow(overflowing: boolean) {
  const proto = window.HTMLElement.prototype;
  Object.defineProperty(proto, 'scrollHeight', {
    configurable: true,
    get() { return overflowing ? 200 : 50; },
  });
  Object.defineProperty(proto, 'clientHeight', {
    configurable: true,
    get() { return 50; },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  // @ts-expect-error restoring the happy-dom defaults
  delete window.HTMLElement.prototype.scrollHeight;
  // @ts-expect-error restoring the happy-dom defaults
  delete window.HTMLElement.prototype.clientHeight;
});

describe('ExpandableText', () => {
  it('renders its children', () => {
    mockOverflow(false);
    render(<ExpandableText>Short note</ExpandableText>);
    expect(screen.getByText('Short note')).toBeDefined();
  });

  it('offers no control when the text fits', () => {
    mockOverflow(false);
    render(<ExpandableText>Short note</ExpandableText>);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('offers a control when the text overflows', () => {
    mockOverflow(true);
    render(<ExpandableText>{'long '.repeat(200)}</ExpandableText>);
    expect(screen.getByRole('button', { name: /Show full/ })).toBeDefined();
  });

  it('expands and collapses on click', async () => {
    mockOverflow(true);
    const user = userEvent.setup();
    render(<ExpandableText>{'long '.repeat(200)}</ExpandableText>);

    const toggle = screen.getByRole('button');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    await user.click(toggle);
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button').textContent).toMatch(/Show less/);

    await user.click(screen.getByRole('button'));
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false');
  });

  it('names what expands so the control reads clearly', () => {
    mockOverflow(true);
    render(<ExpandableText label="match">{'long '.repeat(200)}</ExpandableText>);
    expect(screen.getByRole('button').textContent).toBe('Show full match');
  });

  it('clamps to the requested number of lines while collapsed', () => {
    mockOverflow(true);
    const { container } = render(<ExpandableText clampLines={2}>{'long '.repeat(200)}</ExpandableText>);
    expect(container.querySelector('.line-clamp-2')).not.toBeNull();
  });

  it('defaults to a four line clamp', () => {
    mockOverflow(true);
    const { container } = render(<ExpandableText>{'long '.repeat(200)}</ExpandableText>);
    expect(container.querySelector('.line-clamp-4')).not.toBeNull();
  });

  it('drops the clamp once expanded', async () => {
    mockOverflow(true);
    const user = userEvent.setup();
    const { container } = render(<ExpandableText>{'long '.repeat(200)}</ExpandableText>);
    await user.click(screen.getByRole('button'));
    expect(container.querySelector('[class*="line-clamp"]')).toBeNull();
  });

  it('keeps rich children clickable, not flattened to a string', () => {
    mockOverflow(true);
    render(
      <ExpandableText>
        <a href="/patterns/1">Pattern #1</a> matched
      </ExpandableText>,
    );
    expect(screen.getByRole('link', { name: 'Pattern #1' })).toBeDefined();
  });
});
