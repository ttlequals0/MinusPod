/**
 * Unit tests for the local-feed API client functions: addLocalFeed must POST
 * feedType: 'local' alongside the caller's payload, uploadFeedArtwork must
 * post multipart form data (mirrors importOpml).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockApiRequest = vi.fn();

vi.mock('./client', () => ({
  apiRequest: (...a: unknown[]) => mockApiRequest(...a),
  buildQueryString: () => '',
}));

import { addLocalFeed, uploadFeedArtwork } from './feeds';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('addLocalFeed', () => {
  it('posts feedType: local merged with the caller payload', async () => {
    mockApiRequest.mockResolvedValue({
      slug: 'my-show', feedType: 'local', feedUrl: 'https://x/my-show.xml', message: 'created',
    });

    await addLocalFeed({ title: 'My Show', slug: 'my-show' });

    expect(mockApiRequest).toHaveBeenCalledWith('/feeds', {
      method: 'POST',
      body: { feedType: 'local', title: 'My Show', slug: 'my-show' },
    });
  });
});

describe('uploadFeedArtwork', () => {
  it('posts a multipart form with the file under the "file" field', async () => {
    mockApiRequest.mockResolvedValue({ message: 'ok', artworkUrl: '/artwork.jpg' });
    const file = new File(['data'], 'cover.jpg', { type: 'image/jpeg' });

    await uploadFeedArtwork('my-show', file);

    expect(mockApiRequest).toHaveBeenCalledTimes(1);
    const [endpoint, options] = mockApiRequest.mock.calls[0];
    expect(endpoint).toBe('/feeds/my-show/artwork');
    expect(options.method).toBe('POST');
    expect(options.body).toBeInstanceOf(FormData);
    expect((options.body as FormData).get('file')).toBe(file);
  });
});
