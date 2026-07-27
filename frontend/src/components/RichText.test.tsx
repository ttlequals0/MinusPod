/**
 * RichText renders publisher descriptions with links intact.
 *
 * Feed descriptions carry bare URLs in plain text; episode descriptions carry
 * real anchors whose text is often a person's name. Both have to survive, and
 * neither may become a route for injected markup.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import RichText, { isSafeHref } from './RichText';

describe('RichText', () => {
  it('renders nothing for empty input', () => {
    const { container } = render(<RichText html={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('makes a bare URL clickable', () => {
    render(<RichText html="Join us live https://discord.gg/abc123 today" />);
    const link = screen.getByRole('link', { name: 'https://discord.gg/abc123' });
    expect(link.getAttribute('href')).toBe('https://discord.gg/abc123');
  });

  it('keeps an anchor whose text is a name, not a URL', () => {
    render(<RichText html='<a href="https://twit.tv/people/steve-gibson">Steve Gibson</a>' />);
    const link = screen.getByRole('link', { name: 'Steve Gibson' });
    expect(link.getAttribute('href')).toBe('https://twit.tv/people/steve-gibson');
  });

  it('opens links in a new tab without leaking the opener', () => {
    render(<RichText html="see https://example.com/x" />);
    const link = screen.getByRole('link');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('leaves sentence punctuation outside the link', () => {
    render(<RichText html="Details at https://example.com/page." />);
    expect(screen.getByRole('link').getAttribute('href')).toBe('https://example.com/page');
    expect(screen.getByText(/\.$/)).toBeDefined();
  });

  it('handles a URL inside parentheses', () => {
    render(<RichText html="(see https://example.com/x)" />);
    expect(screen.getByRole('link').getAttribute('href')).toBe('https://example.com/x');
  });

  it('keeps paragraph text from running together', () => {
    const { container } = render(<RichText html="<p>First para.</p><p>Second para.</p>" />);
    expect(container.textContent).toContain('First para.');
    expect(container.textContent).toContain('Second para.');
    expect(container.textContent).not.toContain('First para.Second para.');
  });

  it('renders both shapes in one description', () => {
    render(
      <RichText html={'<p>Hosted by <a href="https://twit.tv/people/leo">Leo Laporte</a></p><p>Notes: https://grc.com/sn/notes.pdf</p>'} />,
    );
    expect(screen.getByRole('link', { name: 'Leo Laporte' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'https://grc.com/sn/notes.pdf' })).toBeDefined();
  });

  describe('refuses unsafe hrefs', () => {
    it('does not linkify a javascript: anchor', () => {
      render(<RichText html={'<a href="javascript:alert(1)">click me</a>'} />);
      expect(screen.queryByRole('link')).toBeNull();
      expect(screen.getByText('click me')).toBeDefined();
    });

    it('does not linkify a data: anchor', () => {
      render(<RichText html={'<a href="data:text/html,<script>alert(1)</script>">x</a>'} />);
      expect(screen.queryByRole('link')).toBeNull();
    });

    it('never injects markup from the description', () => {
      const { container } = render(
        <RichText html={'<img src=x onerror="alert(1)"><script>alert(2)</script>safe text'} />,
      );
      expect(container.querySelector('img')).toBeNull();
      expect(container.querySelector('script')).toBeNull();
      expect(container.textContent).toContain('safe text');
    });
  });

  describe('isSafeHref', () => {
    it.each(['https://a.com', 'http://a.com', 'mailto:a@b.com'])('allows %s', (href) => {
      expect(isSafeHref(href)).toBe(true);
    });

    it.each(['javascript:alert(1)', 'data:text/html,x', 'vbscript:x', '', null, undefined])(
      'rejects %s', (href) => {
        expect(isSafeHref(href as string)).toBe(false);
      });
  });
});

describe('RichText block handling', () => {
  it('prefixes list items so they read as a list', () => {
    const { container } = render(<RichText html="<ul><li>First</li><li>Second</li></ul>" />);
    expect(container.textContent).toContain('- First');
    expect(container.textContent).toContain('- Second');
  });

  it('does not open with a blank line', () => {
    const { container } = render(<RichText html="<p>Opening line.</p>" />);
    expect(container.textContent?.startsWith('Opening line.')).toBe(true);
  });

  it('collapses the blank-line run that nested blocks produce', () => {
    const { container } = render(<RichText html="<div><p>One</p></div><div><p>Two</p></div>" />);
    expect(container.textContent).not.toMatch(/\n\s*\n/);
  });
});
