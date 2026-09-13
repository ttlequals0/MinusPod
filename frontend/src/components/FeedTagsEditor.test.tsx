/**
 * Loading-state test for FeedTagsEditor: the tag row holds pill-shaped
 * placeholders while the tags load.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FeedTagsEditor } from './FeedTagsEditor';

const mockGetFeedTags = vi.fn();

vi.mock('../api/community', () => ({
  getFeedTags: (...a: unknown[]) => mockGetFeedTags(...a),
  setFeedUserTags: vi.fn(),
  getTagVocabulary: vi.fn().mockResolvedValue({
    vocabulary_version: 1, all_tags: ['technology'], podcast_genres: [],
    sponsor_industries: [], special_tags: [],
  }),
}));

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FeedTagsEditor slug="example-podcast" />
    </QueryClientProvider>,
  );
}

describe('FeedTagsEditor loading placeholder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetFeedTags.mockResolvedValue({
      effective: ['technology'], rss: ['technology'], episode: [], user: [],
    });
  });

  it('shows tag-shaped skeletons while the tags query is pending', () => {
    mockGetFeedTags.mockReturnValueOnce(new Promise(() => {}));
    renderEditor();
    const placeholder = screen.getByTestId('skeleton-tags');
    expect(placeholder.getAttribute('aria-busy')).toBe('true');
    expect(placeholder.querySelectorAll('.rounded-full').length).toBe(3);
  });

  it('drops the skeletons once the tags land', async () => {
    renderEditor();
    await screen.findByText('From RSS:');
    expect(screen.queryByTestId('skeleton-tags')).toBeNull();
  });
});
