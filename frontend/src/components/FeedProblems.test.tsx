import type { ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FeedProblem, FeedProblemsResponse } from '../api/types';
import FeedProblems from './FeedProblems';

const getFeedProblems = vi.hoisted(() => vi.fn());
const refreshFeed = vi.hoisted(() => vi.fn());

vi.mock('../api/feeds', () => ({ getFeedProblems, refreshFeed }));

function render(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function problem(over: Partial<FeedProblem> = {}): FeedProblem {
  return {
    slug: 'example-podcast', title: 'Example Podcast', host: 'https://feed.example.com',
    consecutiveFailures: 4, errorKind: 'gone',
    message: 'The feed was not found. It may have moved or been removed.',
    detail: 'HTTP 404', failingSince: '2026-07-14T00:00:00Z',
    lastSuccessAt: '2026-07-13T00:00:00Z', nextRetryAt: '2026-07-14T06:00:00Z', ...over,
  };
}

function respond(problems: FeedProblem[]): FeedProblemsResponse {
  return { problems, count: problems.length, threshold: 3 };
}

describe('FeedProblems', () => {
  beforeEach(() => {
    getFeedProblems.mockReset();
    refreshFeed.mockReset();
  });

  it('shows one calm line when nothing is failing', async () => {
    getFeedProblems.mockResolvedValue(respond([]));
    render(<FeedProblems />);
    expect(await screen.findByText('No feeds are failing.')).toBeDefined();
  });

  it('lists each failing feed with its plain message, failures and times', async () => {
    getFeedProblems.mockResolvedValue(respond([problem(), problem({ slug: 'b', title: 'Second Show', consecutiveFailures: 3 })]));
    render(<FeedProblems />);
    expect(await screen.findByText(/Example Podcast/)).toBeDefined();
    expect(screen.getAllByText('https://feed.example.com')).toHaveLength(2);
    expect(screen.getAllByText(/not found/)).toHaveLength(2);
    expect(screen.getByText(/4 failures in a row/)).toBeDefined();
    expect(screen.getAllByText(/last success/)).toHaveLength(2);
    expect(screen.getAllByText(/next retry/)).toHaveLength(2);
    expect(screen.getByText('2')).toBeDefined();
  });

  it('refreshes one feed and shows a pending state until it settles', async () => {
    getFeedProblems.mockResolvedValue(respond([problem(), problem({ slug: 'b', title: 'Second Show' })]));
    let release: (value: { message: string }) => void = () => {};
    refreshFeed.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    render(<FeedProblems />);
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh Example Podcast now' }));
    expect(await screen.findByText('Refreshing...')).toBeDefined();
    expect((screen.getByRole('button', { name: 'Refresh Example Podcast now' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Refresh Second Show now' }) as HTMLButtonElement).disabled).toBe(false);
    expect(refreshFeed).toHaveBeenCalledWith('example-podcast');
    getFeedProblems.mockResolvedValue(respond([]));
    release({ message: 'ok' });
    expect(await screen.findByText('No feeds are failing.')).toBeDefined();
  });

  it('shows the failure under the row when the retry fails', async () => {
    getFeedProblems.mockResolvedValue(respond([problem()]));
    refreshFeed.mockRejectedValue(new Error('Failed to fetch RSS feed: HTTP 404'));
    render(<FeedProblems />);
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh Example Podcast now' }));
    expect((await screen.findByRole('alert')).textContent).toContain('HTTP 404');
    await waitFor(() => expect((screen.getByRole('button', { name: 'Refresh Example Podcast now' }) as HTMLButtonElement).disabled).toBe(false));
  });

  it('offers a retry when the list cannot load', async () => {
    getFeedProblems.mockRejectedValueOnce(new Error('boom'));
    render(<FeedProblems />);
    expect(await screen.findByText('Could not load feed problems.')).toBeDefined();
    getFeedProblems.mockResolvedValue(respond([]));
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No feeds are failing.')).toBeDefined();
  });
});
