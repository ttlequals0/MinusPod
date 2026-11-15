import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import MatchNote from './MatchNote';
import type { AdSegment } from '../api/types';

function renderNote(partial: Partial<AdSegment>) {
  const segment = { start: 0, end: 10, confidence: 0.9, ...partial } as AdSegment;
  return render(<MemoryRouter><MatchNote segment={segment} fallback={null} /></MemoryRouter>);
}

describe('MatchNote', () => {
  it('names what a learned pattern was learned from and links it', () => {
    renderNote({
      reason: 'Pattern #1 (content "x" 89%)',
      patternSummary: { id: 1, templateStart: 'one two three four five six seven eight', confirmedCount: 3, matchCount: 9, createdBy: 'show_promo' },
    });
    const link = screen.getByRole('link');
    expect(link.textContent).toBe('Learned from this show: one two three four five six seven eight (confirmed by you 3 times)');
    expect(link.getAttribute('href')).toBe('/patterns?id=1');
  });

  it('says matched, with the match count, when nobody confirmed the pattern', () => {
    renderNote({
      reason: 'Pattern #1 (content "x" 89%)',
      patternSummary: { id: 1, templateStart: 'one two three', confirmedCount: 0, matchCount: 4, createdBy: 'show_promo' },
    });
    expect(screen.getByRole('link').textContent).toBe('Learned from this show: one two three (matched 4 times)');
  });

  it('leaves a marker without a learned pattern to the fallback', () => {
    const segment = { start: 0, end: 10, confidence: 0.9, reason: 'Pattern #1 (content "some text" 89%)' } as AdSegment;
    render(<MemoryRouter><MatchNote segment={segment} fallback={<span>upstream text</span>} /></MemoryRouter>);
    expect(screen.getByText('upstream text')).not.toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
  });
});
