import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AccountSwitchPreflight from './AccountSwitchPreflight';

const mockGetAffectedRuns = vi.fn();
vi.mock('../../api/providers', () => ({
  getAffectedRuns: (...args: unknown[]) => mockGetAffectedRuns(...args),
}));

function renderPreflight(changed = true, onActionChange = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AccountSwitchPreflight
        slot="secondary"
        changed={changed}
        action="requeue"
        onActionChange={onActionChange}
      />
    </QueryClientProvider>,
  );
}

describe('AccountSwitchPreflight', () => {
  beforeEach(() => vi.clearAllMocks());

  it('asks for nothing until the endpoint or provider type actually changes', () => {
    renderPreflight(false);
    expect(mockGetAffectedRuns).not.toHaveBeenCalled();
  });

  it('lists the affected runs and defaults to requeueing them', async () => {
    mockGetAffectedRuns.mockResolvedValue({
      count: 2,
      runs: [
        { id: 'r1', slug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', title: 'One', state: 'active' },
        { id: 'r2', slug: 'example-podcast', episodeId: 'f6e5d4c3b2a1', title: 'Two', state: 'queued' },
      ],
    });
    renderPreflight();

    expect(await screen.findByText(/2 runs are still bound/)).toBeTruthy();
    expect(screen.getByText(/One/)).toBeTruthy();
    const requeue = screen.getByRole('radio', { name: 'Requeue on the new account' });
    expect(requeue.getAttribute('aria-checked')).toBe('true');
  });

  it('says so and blocks nothing when the preflight is unavailable', async () => {
    mockGetAffectedRuns.mockRejectedValue(new Error('404 Not Found'));
    renderPreflight();

    expect(await screen.findByText(/cannot list the runs bound to the current account/)).toBeTruthy();
    expect(screen.queryByRole('radiogroup')).toBeNull();
  });

  it('states plainly when nothing is in flight', async () => {
    mockGetAffectedRuns.mockResolvedValue({ count: 0, runs: [] });
    renderPreflight();

    expect(await screen.findByText(/No runs are bound to the current secondary account/)).toBeTruthy();
  });
});
