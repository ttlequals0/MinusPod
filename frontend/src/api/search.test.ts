/**
 * search() omits groups by default (Advanced page: server computes all five) and
 * passes it through untouched when a caller (the unified hook) supplies one.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockApiRequest = vi.fn();

vi.mock('./client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./client')>()),
  apiRequest: (...a: unknown[]) => mockApiRequest(...a),
}));

import { search } from './search';

beforeEach(() => {
  vi.clearAllMocks();
  mockApiRequest.mockResolvedValue({
    query: 'x', shows: [], episodes: [], transcripts: [], patterns: [], sponsors: [],
  });
});

describe('search', () => {
  it('omits groups when not passed, so the server defaults to all five', async () => {
    await search('ba', 8);
    const [endpoint] = mockApiRequest.mock.calls[0];
    expect(endpoint).toBe('/search?q=ba&limit=8');
  });

  it('forwards a groups value as-is', async () => {
    await search('ba', 8, undefined, 'shows,episodes,transcripts');
    const [endpoint] = mockApiRequest.mock.calls[0];
    expect(endpoint).toBe('/search?q=ba&limit=8&groups=shows%2Cepisodes%2Ctranscripts');
  });
});
