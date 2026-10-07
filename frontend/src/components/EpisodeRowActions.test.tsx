import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { bulkEpisodeAction, reprocessEpisode } from '../api/feeds';
import EpisodeRowActions from './EpisodeRowActions';

vi.mock('../api/feeds', () => ({
  reprocessEpisode: vi.fn(async () => ({ message: 'ok', mode: 'reprocess' as const })),
  bulkEpisodeAction: vi.fn(async () => ({ queued: 1, skipped: 0, freedMb: 1, errors: [] })),
}));

const reprocessMock = reprocessEpisode as unknown as ReturnType<typeof vi.fn>;
const bulkMock = bulkEpisodeAction as unknown as ReturnType<typeof vi.fn>;

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

function triggerButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: 'Actions' });
}

describe('EpisodeRowActions: action label stays stable across run states', () => {
  it('offers Process for an episode that has never completed', async () => {
    const user = userEvent.setup();
    renderTrigger({ status: 'pending' });
    await user.click(triggerButton());
    expect(screen.getByRole('menuitem', { name: /Process/ })).toBeTruthy();
  });

  it('offers Reprocess and Full Analysis for a completed episode', async () => {
    const user = userEvent.setup();
    renderTrigger({ status: 'completed' });
    await user.click(triggerButton());
    expect(screen.getByRole('menuitem', { name: /Reprocess/ })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: /Full Analysis/ })).toBeTruthy();
  });

  it.each(['submitting', 'queued', 'processing'] as const)(
    'never relabels the trigger when jobState is %s',
    (jobState) => {
      renderTrigger({ status: 'completed', jobState });
      expect(triggerButton()).toBeTruthy();
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
      expect(triggerButton().disabled).toBe(true);
    },
  );

  it('leaves the trigger enabled when jobState is idle', () => {
    renderTrigger({ status: 'completed', jobState: 'idle' });
    expect(triggerButton().disabled).toBe(false);
  });
});

describe('EpisodeRowActions: equal-width trigger', () => {
  it('applies the same min-width floor to the trigger regardless of label', () => {
    const { unmount } = renderTrigger({ status: 'pending' });
    const processClass = triggerButton().className;
    unmount();
    renderTrigger({ status: 'completed' });
    const reprocessClass = triggerButton().className;
    expect(processClass).toBe(reprocessClass);
    // The floor only equalizes widths while the type stays text-xs.
    expect(processClass).toMatch(/(^|\s)min-w-24(\s|$)/);
    expect(processClass).toMatch(/(^|\s)text-xs(\s|$)/);
    expect(processClass).toMatch(/(^|\s)min-h-11(\s|$)/);
  });
});

describe('EpisodeRowActions: accessible name', () => {
  it('names the trigger with its visible label and keeps the tooltip separate', () => {
    renderTrigger({ status: 'completed' });
    const trigger = screen.getByRole('button', { name: 'Actions' });
    expect(trigger.getAttribute('title')).toBe('Episode actions');
    expect(trigger.getAttribute('aria-label')).toBeNull();
  });
});

describe('EpisodeRowActions: label follows hasBeenProcessed, not status', () => {
  it('keeps the Actions trigger disabled when a previously processed episode is queued again', () => {
    renderTrigger({ status: 'pending', jobState: 'queued', hasBeenProcessed: true });
    expect(triggerButton().disabled).toBe(true);
  });

  it('offers Reprocess after a failed reprocess', async () => {
    const user = userEvent.setup();
    renderTrigger({ status: 'failed', jobState: 'idle', hasBeenProcessed: true });
    await user.click(triggerButton());
    expect(screen.getByRole('menuitem', { name: /Reprocess/ })).toBeTruthy();
  });

  it('offers Process when the episode has never been processed', async () => {
    const user = userEvent.setup();
    renderTrigger({ status: 'completed', jobState: 'idle', hasBeenProcessed: false });
    await user.click(triggerButton());
    expect(screen.getByRole('menuitem', { name: /^Process/ })).toBeTruthy();
  });
});

describe('EpisodeRowActions: downloaded file deletion', () => {
  beforeEach(() => {
    bulkMock.mockReset();
    bulkMock.mockResolvedValue({ queued: 1, skipped: 0, freedMb: 1, errors: [] });
  });

  async function openDelete(user: ReturnType<typeof userEvent.setup>) {
    await user.click(triggerButton());
    await user.click(screen.getByRole('menuitem', { name: /^Delete/ }));
  }

  it('offers Delete only for processed rows eligible for the endpoint', async () => {
    const user = userEvent.setup();
    const { rerender, client } = renderTrigger({ status: 'completed', hasBeenProcessed: true });
    await openDelete(user);
    expect(screen.getByRole('dialog')).toBeTruthy();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();

    rerender(
      <QueryClientProvider client={client}>
        <EpisodeRowActions feedSlug="show" episodeId="ep-1" status="pending" hasBeenProcessed />
      </QueryClientProvider>,
    );
    await user.click(triggerButton());
    expect(screen.queryByRole('menuitem', { name: /^Delete/ })).toBeNull();
  });

  it.each(['completed', 'failed', 'permanently_failed', 'deferred'] as const)(
    'offers Delete for a processed %s episode', async (status) => {
      const user = userEvent.setup();
      renderTrigger({ status, hasBeenProcessed: true });
      await openDelete(user);
      expect(screen.getByRole('dialog')).toBeTruthy();
    },
  );

  it('rechecks the job guard while the confirmation is open', async () => {
    const user = userEvent.setup();
    const { rerender, client } = renderTrigger({ status: 'completed', jobState: 'idle', hasBeenProcessed: true });
    await openDelete(user);
    rerender(
      <QueryClientProvider client={client}>
        <EpisodeRowActions feedSlug="show" episodeId="ep-1" status="completed" jobState="processing" hasBeenProcessed />
      </QueryClientProvider>,
    );
    expect((screen.getByRole('button', { name: 'Delete files' }) as HTMLButtonElement).disabled).toBe(true);
    expect(bulkMock).not.toHaveBeenCalled();
  });

  it('cancel leaves the episode unchanged', async () => {
    const user = userEvent.setup();
    renderTrigger({ status: 'completed', hasBeenProcessed: true });
    await openDelete(user);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(bulkMock).not.toHaveBeenCalled();
  });

  it('keeps the confirmation open and locks Escape and Cancel while pending', async () => {
    const user = userEvent.setup();
    let resolveDelete!: (value: { queued: number; skipped: number; freedMb: number; errors: string[] }) => void;
    bulkMock.mockReturnValue(new Promise((resolve) => { resolveDelete = resolve; }));
    renderTrigger({ status: 'completed', hasBeenProcessed: true });
    await openDelete(user);
    await user.click(screen.getByRole('button', { name: 'Delete files' }));
    expect(await screen.findByRole('button', { name: 'Deleting...' })).toBeTruthy();
    expect(triggerButton().disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeTruthy();
    resolveDelete({ queued: 1, skipped: 0, freedMb: 1, errors: [] });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('keeps the dialog open and explains a skipped HTTP 200 deletion', async () => {
    const user = userEvent.setup();
    bulkMock.mockResolvedValue({ queued: 0, skipped: 1, freedMb: 0, errors: [] });
    renderTrigger({ status: 'completed', hasBeenProcessed: true });
    await openDelete(user);
    await user.click(screen.getByRole('button', { name: 'Delete files' }));
    expect((await screen.findByRole('alert')).textContent).toContain('No files were deleted.');
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('keeps the dialog open when the endpoint reports an error', async () => {
    const user = userEvent.setup();
    bulkMock.mockResolvedValue({ queued: 0, skipped: 0, freedMb: 0, errors: ['bulk delete failed'] });
    renderTrigger({ status: 'completed', hasBeenProcessed: true });
    await openDelete(user);
    await user.click(screen.getByRole('button', { name: 'Delete files' }));
    expect((await screen.findByRole('alert')).textContent).toContain('bulk delete failed');
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('shows a request failure without closing the confirmation', async () => {
    const user = userEvent.setup();
    bulkMock.mockRejectedValue(new Error('Request failed'));
    renderTrigger({ status: 'completed', hasBeenProcessed: true });
    await openDelete(user);
    await user.click(screen.getByRole('button', { name: 'Delete files' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Request failed');
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('invalidates dashboard, feed, list, and detail data after success', async () => {
    const user = userEvent.setup();
    const { client } = renderTrigger({ status: 'completed', hasBeenProcessed: true });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await openDelete(user);
    await user.click(screen.getByRole('button', { name: 'Delete files' }));
    expect((await screen.findByRole('status')).textContent).toContain('Downloaded files were deleted.');
    await waitFor(() => {
      for (const queryKey of [
        ['feeds'], ['feed', 'show'], ['episodes', 'show'], ['episode', 'show', 'ep-1'],
      ]) {
        expect(invalidate).toHaveBeenCalledWith({ queryKey });
      }
    });
    expect(bulkMock).toHaveBeenCalledWith('show', ['ep-1'], 'delete');
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

    await user.click(triggerButton());
    await user.click(screen.getByRole('menuitem', { name: /Use patterns \+ AI/ }));

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

    await user.click(triggerButton());
    await user.click(screen.getByRole('menuitem', { name: /Use patterns \+ AI/ }));

    await waitFor(() => {
      expect(client.getQueryData(['episode', 'show', 'ep-1'])).toMatchObject({ jobState: 'processing' });
    });
  });

  it('applies the jobState a success reported', async () => {
    const user = userEvent.setup();
    reprocessMock.mockResolvedValue({ message: 'queued', mode: 'reprocess', jobState: 'queued' });
    const { client } = renderTrigger({ status: 'completed', jobState: 'idle' });
    client.setQueryData(['episode', 'show', 'ep-1'], { id: 'ep-1', jobState: 'idle' });

    await user.click(triggerButton());
    await user.click(screen.getByRole('menuitem', { name: /Use patterns \+ AI/ }));

    await waitFor(() => {
      expect(client.getQueryData(['episode', 'show', 'ep-1'])).toMatchObject({ jobState: 'queued' });
    });
  });
});
