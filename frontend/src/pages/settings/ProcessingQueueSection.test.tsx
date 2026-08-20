/**
 * Tests for the Processing Queue section: the panel now renders the whole
 * pending queue (positions, "show all" past the preview limit, per-row cancel)
 * instead of the active job alone.
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
    ...overrides,
  };
}

function renderSection(episodes: ProcessingEpisode[] | undefined, onCancel = vi.fn()) {
  render(
    <ProcessingQueueSection
      processingEpisodes={episodes}
      onCancel={onCancel}
      cancelIsPending={false}
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

  it('lists every queued episode with its position', () => {
    renderSection([active(), queued(1), queued(2), queued(3)]);

    expect(screen.getByText('Waiting (3)')).toBeTruthy();
    expect(screen.getByText('Queued Episode 1')).toBeTruthy();
    expect(screen.getByText('Queued Episode 3')).toBeTruthy();
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('counts the whole backlog even when the API caps the rows it returns', async () => {
    const user = userEvent.setup();
    const episodes = Array.from({ length: 12 }, (_, i) => queued(i + 1, { queueTotal: 40 }));
    renderSection(episodes);

    expect(screen.getByText('Waiting (40)')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Show all 12' }));
    expect(screen.getByText('+28 further back in the queue')).toBeTruthy();
  });

  it('collapses a long queue behind a show-all toggle', async () => {
    const user = userEvent.setup();
    const episodes = Array.from({ length: 14 }, (_, i) => queued(i + 1));
    renderSection(episodes);

    expect(screen.getByText('Queued Episode 10')).toBeTruthy();
    expect(screen.queryByText('Queued Episode 11')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Show all 14' }));
    expect(screen.getByText('Queued Episode 14')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Show fewer' }));
    expect(screen.queryByText('Queued Episode 11')).toBeNull();
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
    render(
      <ProcessingQueueSection
        processingEpisodes={[queued(1), queued(2)]}
        onCancel={vi.fn()}
        cancelIsPending
        cancelingKey="pod:ep-2"
      />
    );

    expect(screen.getAllByRole('button', { name: 'Cancel' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Canceling...' })).toBeTruthy();
  });
});
