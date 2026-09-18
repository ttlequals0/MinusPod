/** getAffectedRuns unwraps the nested affectedRuns object so the preflight
 *  gets {count, runs} and never calls .map on undefined. */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockApiRequest = vi.fn();

vi.mock('./client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./client')>()),
  apiRequest: (...a: unknown[]) => mockApiRequest(...a),
}));

import { getAffectedRuns } from './providers';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('getAffectedRuns', () => {
  it('unwraps the affectedRuns object from the response envelope', async () => {
    mockApiRequest.mockResolvedValue({
      slot: 'primary',
      accountId: 'abc123',
      affectedRuns: { count: 0, runs: [] },
    });
    const data = await getAffectedRuns('primary');
    expect(data).toEqual({ count: 0, runs: [] });
    expect(Array.isArray(data.runs)).toBe(true);
  });

  it('returns an empty result when the envelope omits affectedRuns', async () => {
    mockApiRequest.mockResolvedValue({ slot: 'primary', accountId: null });
    const data = await getAffectedRuns('primary');
    expect(data).toEqual({ count: 0, runs: [] });
  });
});
