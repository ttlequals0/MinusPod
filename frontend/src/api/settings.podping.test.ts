import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiRequest = vi.fn();

vi.mock('./client', () => ({
  apiRequest: (...args: unknown[]) => mockApiRequest(...args),
}));

import { requestPodpingCheck } from './settings';

describe('requestPodpingCheck', () => {
  beforeEach(() => mockApiRequest.mockReset());

  it('starts a manual Podping node check', async () => {
    const response = {
      checkId: 'check-1', status: 'pending', requestedAt: '2026-09-20T00:00:00Z',
      startedAt: null, completedAt: null,
    };
    mockApiRequest.mockResolvedValue(response);

    await expect(requestPodpingCheck()).resolves.toBe(response);
    expect(mockApiRequest).toHaveBeenCalledWith('/system/podping/check', { method: 'POST' });
  });
});
