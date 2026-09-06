/**
 * Tests for the Processing Queue section: the active job, the paginated
 * waiting list (offset-aware positions, page controls, per-row priority
 * stepper), and per-row cancel.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProcessingQueueSection from './ProcessingQueueSection';
import type { ProcessingEpisode } from '../../api/settings';

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

function renderSection(
  episodes: ProcessingEpisode[] | undefined,
  onCancel = vi.fn(),
  options: RenderOptions = {},
) {
  render(
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
  );
  return onCancel;
}

describe('ProcessingQueueSection', () => {
  it('shows the empty state when nothing is processing or queued', () => {
    renderSection([]);
    expect(screen.getByText('No episodes processing or queued')).toBeTruthy();
  });

  it('renders the active job with a human-readable stage', () => {
    renderSection([active()]);
    expect(screen.getByText('Active Episode')).toBeTruthy();
    expect(screen.getByText(/Transcribing/)).toBeTruthy();
  });

  it('lists queued episodes with their positions', () => {
    renderSection([active(), queued(1), queued(2), queued(3)]);

    expect(screen.getByText('Waiting (3)')).toBeTruthy();
    expect(screen.getByText('Queued Episode 1')).toBeTruthy();
    expect(screen.getByText('Queued Episode 3')).toBeTruthy();
    expect(screen.getByText('3')).toBeTruthy();
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
    expect(screen.getByText('Queued Episode 30')).toBeTruthy();
    expect(screen.queryByText('Queued Episode 1')).toBeNull();
    expect(screen.getByText('Page 2 of 2 (30 total)')).toBeTruthy();
  });

  it('steps the priority of a reorderable row as a server-side delta', async () => {
    const user = userEvent.setup();
    const onPriorityChange = vi.fn();
    renderSection([queued(1, { priority: 4 })], vi.fn(), { onPriorityChange });

    // Deltas, not priority+n: the list refetches every 5s, so a click made
    // against a stale value must still land on whatever the row now holds.
    await user.click(screen.getByRole('button', { name: 'Raise priority for Queued Episode 1' }));
    expect(onPriorityChange).toHaveBeenCalledWith({
      slug: 'pod', episodeId: 'ep-1', delta: 5,
    });
    await user.click(screen.getByRole('button', { name: 'Lower priority for Queued Episode 1' }));
    expect(onPriorityChange).toHaveBeenLastCalledWith({
      slug: 'pod', episodeId: 'ep-1', delta: -5,
    });
  });

  it('writes an exact priority typed into the row field', async () => {
    const user = userEvent.setup();
    const onPriorityChange = vi.fn();
    renderSection([queued(1, { priority: 4 })], vi.fn(), { onPriorityChange });

    const field = screen.getByRole('spinbutton', { name: 'Priority for Queued Episode 1' });
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

    const cancelButtons = screen.getAllByRole('button', { name: 'Cancel' });
    // [active, queued 1, queued 2]
    await user.click(cancelButtons[2]);

    expect(onCancel).toHaveBeenCalledWith({ slug: 'pod', episodeId: 'ep-2' });
  });

  it('only labels the row being canceled', () => {
    renderSection([queued(1), queued(2)], vi.fn(), {
      cancelIsPending: true,
      cancelingKey: 'pod:ep-2',
    });

    expect(screen.getAllByRole('button', { name: 'Cancel' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Canceling...' })).toBeTruthy();
  });
});
