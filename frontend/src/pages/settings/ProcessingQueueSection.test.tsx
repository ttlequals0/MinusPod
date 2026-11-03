/**
 * Tests for the Processing queue section: the active job, the paginated
 * waiting list (offset-aware positions, page controls, per-row priority
 * stepper), and per-row cancel.
 */
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { btnBase } from '../../components/buttonStyles';
import ProcessingQueueSection from './ProcessingQueueSection';
import type { ProcessingEpisode } from '../../api/settings';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';

const mockGetAdmission = vi.fn();
const mockSetAdmission = vi.fn();

vi.mock('../../api/settings', () => ({
  getProcessingAdmission: (...args: unknown[]) => mockGetAdmission(...args),
  setProcessingAdmission: (...args: unknown[]) => mockSetAdmission(...args),
}));

function active(overrides: Partial<ProcessingEpisode> = {}): ProcessingEpisode {
  return {
    episodeId: 'ep-active',
    slug: 'pod',
    title: 'Active Episode',
    podcast: 'Test Pod',
    startedAt: null,
    stage: 'transcribing',
    ...overrides,
  };
}

function queued(position: number, overrides: Partial<ProcessingEpisode> = {}): ProcessingEpisode {
  return {
    episodeId: `ep-${position}`,
    slug: 'pod',
    title: `Queued Episode ${position}`,
    podcast: 'Test Pod',
    startedAt: null,
    stage: 'queued',
    queuePosition: position,
    priority: 0,
    ...overrides,
  };
}

interface RenderOptions {
  queuePage?: number;
  onQueuePage?: (page: number) => void;
  onPriorityChange?: (
    params: { slug: string; episodeId: string; priority?: number; delta?: number },
  ) => void;
  priorityIsPending?: boolean;
  cancelIsPending?: boolean;
  cancelingKey?: string | null;
}

const table = () => within(screen.getByRole('table'));
const cards = () => within(screen.getByTestId('queue-cards'));

function renderSection(
  episodes: ProcessingEpisode[] | undefined,
  onCancel = vi.fn(),
  options: RenderOptions = {},
) {
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
        <ProcessingQueueSection
        processingEpisodes={episodes}
        onCancel={onCancel}
        cancelIsPending={options.cancelIsPending ?? false}
        cancelingKey={options.cancelingKey}
        queuePage={options.queuePage ?? 1}
        onQueuePage={options.onQueuePage ?? vi.fn()}
        onPriorityChange={options.onPriorityChange ?? vi.fn()}
        priorityIsPending={options.priorityIsPending ?? false}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );
  return onCancel;
}

describe('ProcessingQueueSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetAdmission.mockResolvedValue({ paused: false, activeRuns: 1, queuedEpisodes: 2 });
    mockSetAdmission.mockResolvedValue({ paused: true, activeRuns: 1, queuedEpisodes: 2 });
  });

  it('puts processing admission before the queue controls', async () => {
    renderSection([]);
    await userEvent.click(await screen.findByRole('button', { name: 'Pause new work' }));
    await waitFor(() => expect(mockSetAdmission).toHaveBeenCalledWith(true));
  });
  it('shows the empty state when nothing is processing or queued', () => {
    renderSection([]);
    expect(screen.getByText('No episodes processing or queued')).toBeTruthy();
  });

  it('renders the active job with a human-readable stage', () => {
    renderSection([active()]);
    const link = screen.getByRole('link', { name: 'Active Episode' });
    expect(link.getAttribute('href')).toBe('/feeds/pod/episodes/ep-active');
    expect(screen.getByText(/Transcribing/)).toBeTruthy();
  });

  it('lists queued episodes with their positions', () => {
    renderSection([active(), queued(1), queued(2), queued(3)]);

    expect(screen.getByText('Waiting (3)')).toBeTruthy();
    const link = table().getByRole('link', { name: 'Queued Episode 1' });
    expect(link.getAttribute('href')).toBe('/feeds/pod/episodes/ep-1');
    expect(table().getByText('Queued Episode 3')).toBeTruthy();
    expect(table().getByText('3')).toBeTruthy();
    ['Position', 'Episode', 'Podcast', 'Priority'].forEach((name) =>
      expect(table().getByRole('columnheader', { name })).toBeTruthy());
    expect(cards().getByRole('link', { name: 'Queued Episode 1' }).getAttribute('href')).toBe('/feeds/pod/episodes/ep-1');
  });

  it('does not paginate a backlog that fits one page', () => {
    const episodes = Array.from({ length: 12 }, (_, i) => queued(i + 1));
    renderSection(episodes);
    expect(screen.queryByText(/Page 1 of/)).toBeNull();
  });

  it('paginates a backlog larger than the page size', () => {
    // A real page-2 response: only that page's rows, with their global positions.
    const episodes = Array.from({ length: 5 }, (_, i) =>
      queued(26 + i, { queueTotal: 30 }));
    renderSection(episodes, vi.fn(), { queuePage: 2 });

    expect(screen.getByText('Waiting (30)')).toBeTruthy();
    expect(table().getByText('Queued Episode 30')).toBeTruthy();
    expect(screen.queryByText('Queued Episode 1')).toBeNull();
    expect(screen.getByText('Page 2 of 2 (30 total)')).toBeTruthy();
  });

  it('steps the priority of a reorderable row as a server-side delta', async () => {
    const user = userEvent.setup();
    const onPriorityChange = vi.fn();
    renderSection([queued(1, { priority: 4 })], vi.fn(), { onPriorityChange });

    // Deltas, not priority+n: the list refetches every 5s, so a click made
    // against a stale value must still land on whatever the row now holds.
    const raise = table().getByRole('button', { name: 'Raise priority for Queued Episode 1' });
    expect(raise.className).toContain('min-h-11');
    await user.click(raise);
    expect(onPriorityChange).toHaveBeenCalledWith({
      slug: 'pod', episodeId: 'ep-1', delta: 5,
    });
    await user.click(table().getByRole('button', { name: 'Lower priority for Queued Episode 1' }));
    expect(onPriorityChange).toHaveBeenLastCalledWith({
      slug: 'pod', episodeId: 'ep-1', delta: -5,
    });
  });

  it('writes an exact priority typed into the row field', async () => {
    const user = userEvent.setup();
    const onPriorityChange = vi.fn();
    renderSection([queued(1, { priority: 4 })], vi.fn(), { onPriorityChange });

    const field = table().getByRole('spinbutton', { name: 'Priority for Queued Episode 1' });
    await user.clear(field);
    await user.type(field, '40');
    // commitOn="blur": nothing is written while the user is still typing.
    expect(onPriorityChange).not.toHaveBeenCalled();
    await user.tab();
    expect(onPriorityChange).toHaveBeenCalledWith({
      slug: 'pod', episodeId: 'ep-1', priority: 40,
    });
  });

  it('shows no stepper for display-queue-only rows', () => {
    renderSection([queued(1, { priority: null })]);
    expect(screen.queryByRole('button', { name: /priority for Queued Episode 1/ })).toBeNull();
  });

  it('cancels the queued episode whose row was clicked', async () => {
    const user = userEvent.setup();
    const onCancel = renderSection([active(), queued(1), queued(2)]);

    const cancelButtons = table().getAllByRole('button', { name: 'Cancel' });
    expect(cancelButtons[0].className).toContain(btnBase);
    await user.click(cancelButtons[1]);

    expect(onCancel).toHaveBeenCalledWith({ slug: 'pod', episodeId: 'ep-2' });
  });

  it('only labels the row being canceled', () => {
    renderSection([queued(1), queued(2)], vi.fn(), {
      cancelIsPending: true,
      cancelingKey: 'pod:ep-2',
    });

    expect(table().getAllByRole('button', { name: 'Cancel' })).toHaveLength(1);
    expect(table().getByRole('button', { name: 'Canceling...' })).toBeTruthy();
  });
});

describe('queue admission explanation', () => {
  it('names the blocked phase, slot, reason and reset time', async () => {
    renderSection([queued(1, {
      admission: {
        blocked: true, phase: 'detection', slot: 'secondary',
        reason: 'rate limit reached', resumesAt: '2026-09-15T12:30:00Z',
      },
    })]);

    const note = (await table().findAllByText(/Held on the detection phase, secondary account/))[0];
    expect(note.textContent).toContain('rate limit reached');
    expect(note.textContent).toMatch(/Resumes/);
  });

  it('says nothing when a row reports no block', async () => {
    renderSection([queued(1, {
      admission: { blocked: false, phase: null, slot: null, reason: null, resumesAt: null },
    })]);

    await table().findByText('Queued Episode 1');
    expect(screen.queryByText(/^Held/)).toBeNull();
  });

  it('renders normally when the endpoint reports no admission at all', async () => {
    renderSection([queued(1)]);

    await table().findByText('Queued Episode 1');
    expect(screen.queryByText(/^Held/)).toBeNull();
  });
});
