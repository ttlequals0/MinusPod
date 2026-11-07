import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import EpisodeRowActions from './EpisodeRowActions';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));
vi.mock('../api/feeds', () => ({
  reprocessEpisode: vi.fn(),
  bulkEpisodeAction: vi.fn(),
}));

afterEach(cleanup);

describe('EpisodeRowActions in demo', () => {
  it('disables the actions menu trigger with the write tooltip', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EpisodeRowActions feedSlug="show" episodeId="ep-1" status="completed" />
      </QueryClientProvider>,
    );
    const trigger = screen.getByRole('button', { name: 'Actions' }) as HTMLButtonElement;
    expect(trigger.disabled).toBe(true);
    expect(trigger.parentElement?.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
  });
});
