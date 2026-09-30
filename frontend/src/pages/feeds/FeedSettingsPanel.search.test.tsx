// Search over the feed settings groups, using the real CollapsibleSection.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeedSettingsPanel from './FeedSettingsPanel';
import type { Feed } from '../../api/types';

vi.mock('../../api/feeds', () => ({
  getNetworks: vi.fn().mockResolvedValue([]),
  updateFeed: vi.fn(),
  rerenderSegments: vi.fn(),
  CUE_SCORE_MIN: 0.30,
  CUE_SCORE_MAX: 0.99,
}));

vi.mock('../../api/cueTemplates', () => ({
  listCueTemplates: vi.fn().mockResolvedValue([]),
}));

vi.mock('../../api/settings', () => ({
  getSettings: vi.fn().mockResolvedValue({}),
  getAudioSettings: () => Promise.resolve({ keepOriginalAudio: true }),
}));

vi.mock('../../components/FeedTagsEditor', () => ({
  FeedTagsEditor: () => null,
}));

const SLUG = 'test-feed';
const NEW_GROUPS = [
  'Source and network',
  'Processing',
  'Title and tag rules',
  'Chapters',
  'Served feed and storage',
];
const OLD_GROUPS = ['Segment actions', 'Cue tuning overrides', 'Advanced'];
const GROUPS = [
  'Source and network',
  'Processing',
  'Title and tag rules',
  'Chapters',
  'Served feed and storage',
  'Segment actions',
  'Cue tuning overrides',
  'Advanced',
];

function renderPanel() {
  const feed: Feed = {
    slug: SLUG,
    title: 'Test Feed',
    sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://example.com/modified.xml',
    episodeCount: 3,
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FeedSettingsPanel feed={feed} slug={SLUG} />
    </QueryClientProvider>,
  );
}

function card(title: string) {
  return screen.getByRole('heading', { name: title }).closest('[data-search-key]') as HTMLElement;
}

function toggle(title: string) {
  return screen.getByRole('heading', { name: title }).closest('button') as HTMLElement;
}

describe('FeedSettingsPanel search', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem(`feed-settings-${SLUG}`, 'true');
  });

  it('groups the controls into collapsible sections with per-feed storage keys', () => {
    renderPanel();
    expect(GROUPS.map((g) => card(g).getAttribute('data-search-key'))).toEqual([
      `feed-source-${SLUG}`,
      `feed-processing-${SLUG}`,
      `feed-title-tags-${SLUG}`,
      `feed-chapters-${SLUG}`,
      `feed-output-${SLUG}`,
      `feed-segment-actions-${SLUG}`,
      `feed-cue-tuning-${SLUG}`,
      `feed-advanced-${SLUG}`,
    ]);
  });

  it('opens the new groups on first visit and keeps the older ones collapsed', () => {
    renderPanel();
    for (const g of NEW_GROUPS) expect(toggle(g).getAttribute('aria-expanded')).toBe('true');
    for (const g of OLD_GROUPS) expect(toggle(g).getAttribute('aria-expanded')).toBe('false');
  });

  it('remembers a collapsed group across remounts', async () => {
    const user = userEvent.setup();
    const { unmount } = renderPanel();
    await user.click(toggle('Chapters'));
    expect(localStorage.getItem(`feed-chapters-${SLUG}`)).toBe('false');
    unmount();
    renderPanel();
    expect(toggle('Chapters').getAttribute('aria-expanded')).toBe('false');
    expect(toggle('Processing').getAttribute('aria-expanded')).toBe('true');
  });

  it('Expand all and Collapse all toggle every group', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: 'Expand all' }));
    for (const g of GROUPS) expect(toggle(g).getAttribute('aria-expanded')).toBe('true');
    await user.click(screen.getByRole('button', { name: 'Collapse all' }));
    for (const g of GROUPS) expect(toggle(g).getAttribute('aria-expanded')).toBe('false');
  });

  it('disables the bulk controls while a search is active', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByRole('textbox', { name: 'Search feed settings' }), 'chapters');
    expect(screen.getByRole('button', { name: 'Expand all' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Collapse all' })).toHaveProperty('disabled', true);
    await user.click(screen.getByRole('button', { name: 'Clear feed settings search' }));
    expect(screen.getByRole('button', { name: 'Expand all' })).toHaveProperty('disabled', false);
  });

  it('typing a label hides the other groups and expands the matching one', async () => {
    localStorage.setItem(`feed-processing-${SLUG}`, 'false');
    const user = userEvent.setup();
    renderPanel();
    expect(toggle('Processing').getAttribute('aria-expanded')).toBe('false');
    await user.type(screen.getByRole('textbox', { name: 'Search feed settings' }), 'queue priority');

    expect(card('Processing').className).not.toContain('hidden');
    expect(toggle('Processing').getAttribute('aria-expanded')).toBe('true');
    for (const g of GROUPS.filter((x) => x !== 'Processing')) {
      expect(card(g).className).toContain('hidden');
    }
  });

  it('clearing the search restores every group to its stored state', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByRole('textbox', { name: 'Search feed settings' }), 'retention');
    await user.click(screen.getByRole('button', { name: 'Clear feed settings search' }));

    for (const g of GROUPS) expect(card(g).className).not.toContain('hidden');
    for (const g of NEW_GROUPS) expect(toggle(g).getAttribute('aria-expanded')).toBe('true');
    for (const g of OLD_GROUPS) expect(toggle(g).getAttribute('aria-expanded')).toBe('false');
  });

  it('shows the empty state when nothing matches', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByRole('textbox', { name: 'Search feed settings' }), 'zzz-nothing');

    expect(screen.getByText('No settings match "zzz-nothing".')).toBeDefined();
    for (const g of GROUPS) expect(card(g).className).toContain('hidden');
  });
});
