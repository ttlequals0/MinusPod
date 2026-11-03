import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WhatsNewDialog from './WhatsNewDialog';

const SECTIONS = [
  { version: '2.101.17', date: '2026-11-04', body: '### Added\n- Shows a `whats_new` dialog (#123)\n- Second **bold** item\n\nPlain paragraph.' },
  { version: '2.101.16', date: '2026-11-03', body: '### Fixed\n- Older fix' },
];

describe('WhatsNewDialog', () => {
  it('renders the title, a block per section and the markdown subset', () => {
    render(<WhatsNewDialog current="2.101.17" sections={SECTIONS} onClose={vi.fn()} />);
    expect(screen.getByRole('dialog', { name: "What's new in 2.101.17" })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '2.101.17 2026-11-04' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '2.101.16 2026-11-03' })).toBeTruthy();
    expect(screen.getByText('Added').tagName).toBe('P');
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(screen.getByText('whats_new').tagName).toBe('CODE');
    expect(screen.getByText('bold').tagName).toBe('STRONG');
    expect(screen.getByText('Plain paragraph.').tagName).toBe('P');
  });

  it('links pull request references and the footer destinations', () => {
    render(<WhatsNewDialog current="2.101.17" sections={SECTIONS} onClose={vi.fn()} />);
    const ref = screen.getByRole('link', { name: '#123' });
    expect(ref.getAttribute('href')).toBe('https://github.com/ttlequals0/MinusPod/pull/123');
    expect(ref.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByRole('link', { name: 'MinusPod on GitHub' }).getAttribute('href'))
      .toBe('https://github.com/ttlequals0/MinusPod');
    expect(screen.getByRole('link', { name: 'Release notes' }).getAttribute('href'))
      .toBe('https://github.com/ttlequals0/MinusPod/releases');
  });

  it('calls onClose from Close', async () => {
    const onClose = vi.fn();
    render(<WhatsNewDialog current="2.101.17" sections={SECTIONS} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows an error instead of an empty claim when loading failed', () => {
    render(<WhatsNewDialog current="2.101.17" sections={undefined} isError onClose={vi.fn()} />);
    expect(screen.getByRole('alert').textContent).toBe('Could not load the changelog.');
  });
});
