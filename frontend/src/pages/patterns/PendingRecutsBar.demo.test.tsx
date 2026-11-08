import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PendingRecutsBar } from './PendingRecutsBar';
import { DEMO_WRITE_TOOLTIP } from '../../demo/copy';

vi.mock('../../demo/flag', () => ({ IS_DEMO: true }));
vi.mock('../../api/settings', () => ({
  getPendingRecuts: vi.fn(async () => ({
    count: 1,
    episodes: [{
      slug: 'example-podcast', episodeId: 'ep-0', title: 'Episode 0',
      podcast: 'Example Podcast', pendingSince: '2026-08-30T00:00:00Z',
      recutReady: true, inFlight: false,
    }],
  })),
  applyPendingRecuts: vi.fn(),
}));

afterEach(cleanup);

describe('PendingRecutsBar in demo', () => {
  test('disables Apply recuts with the write tooltip', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PendingRecutsBar />
      </QueryClientProvider>,
    );
    const apply = await screen.findByRole('button', { name: /apply recuts/i }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    expect(apply.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
  });
});
