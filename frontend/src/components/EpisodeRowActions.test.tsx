import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { reprocessEpisode } from '../api/feeds';
import EpisodeRowActions from './EpisodeRowActions';

vi.mock('../api/feeds', () => ({
  reprocessEpisode: vi.fn(async () => ({ message: 'ok', mode: 'reprocess' as const })),
}));

const reprocessMock = reprocessEpisode as unknown as ReturnType<typeof vi.fn>;

// The trigger's aria-label is "<Process|Reprocess> episode", so its visible
// label is looked up by text and traced to the enclosing <button>.
function renderTrigger(props: Partial<React.ComponentProps<typeof EpisodeRowActions>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <EpisodeRowActions feedSlug="show" episodeId="ep-1" status="completed" {...props} />
    </QueryClientProvider>,
  );
  return { ...view, client };
}

function triggerButton(label: string): HTMLButtonElement {
  return screen.getByText(label).closest('button') as HTMLButtonElement;
}

describe('EpisodeRowActions: action label stays stable across run states', () => {
  it('shows "Process" for an episode that has never completed', () => {
    renderTrigger({ status: 'pending' });
    expect(triggerButton('Process')).toBeTruthy();
  });

  it('shows "Reprocess" for a completed episode', () => {
    renderTrigger({ status: 'completed' });
    expect(triggerButton('Reprocess')).toBeTruthy();
  });

  it.each(['submitting', 'queued', 'processing'] as const)(
    'never relabels the trigger when jobState is %s',
    (jobState) => {
      renderTrigger({ status: 'completed', jobState });
      expect(triggerButton('Reprocess')).toBeTruthy();
      expect(screen.queryByText('Queued')).toBeNull();
      expect(screen.queryByText(/Processing\.\.\./)).toBeNull();
      expect(screen.queryByText(/Reprocessing\.\.\./)).toBeNull();
      expect(screen.queryByText(/Submitting\.\.\./)).toBeNull();
    },
  );

  it.each(['submitting', 'queued', 'processing'] as const)(
    'disables the trigger when jobState is %s',
    (jobState) => {
      renderTrigger({ status: 'completed', jobState });
      expect(triggerButton('Reprocess').disabled).toBe(true);
    },
  );

  it('leaves the trigger enabled when jobState is idle', () => {
    renderTrigger({ status: 'completed', jobState: 'idle' });
    expect(triggerButton('Reprocess').disabled).toBe(false);
  });
});

describe('EpisodeRowActions: equal-width trigger', () => {
  it('applies the same min-width class to the trigger regardless of label', () => {
    const { unmount } = renderTrigger({ status: 'pending' });
    const processClass = triggerButton('Process').className;
    unmount();
    renderTrigger({ status: 'completed' });
    const reprocessClass = triggerButton('Reprocess').className;
    expect(processClass).toBe(reprocessClass);
    expect(processClass).toMatch(/min-w-\[/);
  });
});

describe('EpisodeRowActions: label follows hasBeenProcessed, not status', () => {
  it('stays "Reprocess" for a completed episode that is queued again', () => {
    renderTrigger({ status: 'pending', jobState: 'queued', hasBeenProcessed: true });
    expect(triggerButton('Reprocess')).toBeTruthy();
    expect(screen.queryByText('Process')).toBeNull();
  });

  it('stays "Reprocess" after a failed reprocess', () => {
    renderTrigger({ status: 'failed', jobState: 'idle', hasBeenProcessed: true });
    expect(triggerButton('Reprocess')).toBeTruthy();
  });

  it('reads "Process" when the episode has never been processed', () => {
    renderTrigger({ status: 'completed', jobState: 'idle', hasBeenProcessed: false });
    expect(triggerButton('Process')).toBeTruthy();
  });
});

describe('EpisodeRowActions: failed enqueue', () => {
  beforeEach(() => {
    reprocessMock.mockReset();
  });

  it('surfaces the error instead of looking like a no-op', async () => {
    const user = userEvent.setup();
    reprocessMock.mockRejectedValue(new Error('Queue is unavailable'));
    renderTrigger({ status: 'completed', jobState: 'idle' });

    await user.click(triggerButton('Reprocess'));
    await user.click(screen.getByText('Use patterns + AI'));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Failed');
    expect(alert.getAttribute('title')).toBe('Queue is unavailable');
  });

  it('applies the jobState a 409 reported and blocks the control', async () => {
    const user = userEvent.setup();
    reprocessMock.mockRejectedValue(
      new ApiError('Episode is currently processing', 409, { jobState: 'processing' }),
    );
    const { client } = renderTrigger({ status: 'completed', jobState: 'idle' });
    client.setQueryData(['episode', 'show', 'ep-1'], { id: 'ep-1', jobState: 'idle' });

    await user.click(triggerButton('Reprocess'));
    await user.click(screen.getByText('Use patterns + AI'));

    await waitFor(() => {
      expect(client.getQueryData(['episode', 'show', 'ep-1'])).toMatchObject({ jobState: 'processing' });
    });
  });

  it('applies the jobState a success reported', async () => {
    const user = userEvent.setup();
    reprocessMock.mockResolvedValue({ message: 'queued', mode: 'reprocess', jobState: 'queued' });
    const { client } = renderTrigger({ status: 'completed', jobState: 'idle' });
    client.setQueryData(['episode', 'show', 'ep-1'], { id: 'ep-1', jobState: 'idle' });

    await user.click(triggerButton('Reprocess'));
    await user.click(screen.getByText('Use patterns + AI'));

    await waitFor(() => {
      expect(client.getQueryData(['episode', 'show', 'ep-1'])).toMatchObject({ jobState: 'queued' });
    });
  });
});
