import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import EpisodeRowActions from './EpisodeRowActions';

vi.mock('../api/feeds', () => ({
  reprocessEpisode: vi.fn(async () => ({ message: 'ok', mode: 'reprocess' as const })),
}));

// The trigger's aria-label is "<Process|Reprocess> episode", so its visible
// label is looked up by text and traced to the enclosing <button>.
function renderTrigger(props: Partial<React.ComponentProps<typeof EpisodeRowActions>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EpisodeRowActions feedSlug="show" episodeId="ep-1" status="completed" {...props} />
    </QueryClientProvider>,
  );
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
