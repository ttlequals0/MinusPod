import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import SearchResults, { buildSearchResultRows, UnifiedSearchGroups } from './SearchResults';

const results: UnifiedSearchGroups = {
  shows: [{ slug: 'example-podcast', title: 'The Daily Tech Show', snippet: 'mentions <mark>batteries</mark>' }],
  episodes: [
    { feedSlug: 'example-podcast', feedTitle: 'The Daily Tech Show', episodeId: 'a1b2c3', title: 'Batteries again',
      status: 'permanently_failed', publishDate: null, snippet: null },
  ],
  transcripts: [
    { feedSlug: 'example-podcast', episodeId: 'a1b2c3', title: 'Batteries again',
      snippet: '... about <mark>batteries</mark> at grid scale ...', timestamp: 67 },
    { feedSlug: 'example-podcast', episodeId: 'd4e5f6', title: 'Grid storage',
      snippet: '... flow <mark>batteries</mark> and cost ...', timestamp: null },
  ],
};

const rows = buildSearchResultRows(results);

function renderResults(activeIndex = 0) {
  const onHover = vi.fn();
  const onSelect = vi.fn();
  render(
    <ul role="listbox" aria-label="Results">
      <SearchResults rows={rows} activeIndex={activeIndex} onHover={onHover} onSelect={onSelect} />
    </ul>,
  );
  return { onHover, onSelect };
}

describe('SearchResults', () => {
  it('renders groups in order: Shows, Episodes, In transcripts', () => {
    renderResults();
    const headers = screen.getAllByRole('presentation').map((el) => el.textContent);
    expect(headers).toEqual(['Shows', 'Episodes', 'In transcripts']);
  });

  it('never renders patterns or sponsors groups', () => {
    renderResults();
    expect(screen.queryByText('Patterns')).toBeNull();
    expect(screen.queryByText('Sponsors')).toBeNull();
  });

  it('shows the human label on the episode status chip', () => {
    renderResults();
    expect(screen.getByText('permanently failed')).toBeTruthy();
  });

  it('renders a <mark> in a snippet as an element, not escaped text', () => {
    const { container } = render(
      <ul role="listbox">
        <SearchResults rows={rows} activeIndex={0} onHover={vi.fn()} onSelect={vi.fn()} />
      </ul>,
    );
    const marks = container.querySelectorAll('mark');
    expect(marks.length).toBeGreaterThan(0);
    expect(marks[0].textContent).toBe('batteries');
    expect(container.textContent).not.toContain('<mark>');
  });

  it('reflects the active index with aria-selected', () => {
    renderResults(2);
    const options = screen.getAllByRole('option');
    options.forEach((opt, i) => {
      expect(opt.getAttribute('aria-selected')).toBe(i === 2 ? 'true' : 'false');
    });
  });

  it('renders one option per result', () => {
    renderResults();
    expect(screen.getAllByRole('option')).toHaveLength(4);
  });

  it('renders the timestamp when present and nothing when absent', () => {
    renderResults();
    const timestamps = screen.getAllByTestId('timestamp');
    expect(timestamps).toHaveLength(1);
    expect(timestamps[0].textContent).toBe('1:07');
  });
});
