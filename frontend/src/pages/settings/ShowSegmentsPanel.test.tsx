import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ShowSegmentsPanel from './ShowSegmentsPanel';

const mockSummary = vi.fn();
const mockSet = vi.fn();
const mockRemove = vi.fn();

vi.mock('../../api/communitySegments', () => ({
  getCommunitySegmentsSummary: (...a: unknown[]) => mockSummary(...a),
  setCommunitySegmentsEnabled: (...a: unknown[]) => mockSet(...a),
  removeAllCommunitySegments: (...a: unknown[]) => mockRemove(...a),
}));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ShowSegmentsPanel />
    </QueryClientProvider>,
  );
}

describe('ShowSegmentsPanel', () => {
  beforeEach(() => {
    mockSummary.mockReset();
    mockSet.mockReset();
    mockRemove.mockReset();
  });

  it('shows the switch off by default and the counts line', async () => {
    mockSummary.mockResolvedValue({ enabled: false, counts: { candidate: 2, adopted: 1 }, lastRun: null, lastSummary: null });
    renderPanel();
    const toggle = await screen.findByRole('switch', { name: 'Include show-bound segments' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText('Entries: 2 candidate, 1 adopted.')).toBeTruthy();
  });

  it('saves the switch and shows the saved state', async () => {
    mockSummary
      .mockResolvedValueOnce({ enabled: false, counts: {}, lastRun: null, lastSummary: null })
      .mockResolvedValue({ enabled: true, counts: {}, lastRun: null, lastSummary: null });
    mockSet.mockResolvedValue({ enabled: true, counts: {}, lastRun: null, lastSummary: null });
    renderPanel();
    const toggle = await screen.findByRole('switch', { name: 'Include show-bound segments' });
    await userEvent.click(toggle);
    await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    expect(mockSet).toHaveBeenCalledWith(true, expect.anything());
  });

  it('removes all after a confirm', async () => {
    mockSummary.mockResolvedValue({ enabled: true, counts: { candidate: 1 }, lastRun: null, lastSummary: null });
    mockRemove.mockResolvedValue({ deleted: 1 });
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove all show segments' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove all show segments' }));
    expect(await screen.findByText('Removed 1 show segment.')).toBeTruthy();
  });

  it('renders nothing when the server does not offer the feature', async () => {
    mockSummary.mockRejectedValue(new Error('404'));
    const { container } = renderPanel();
    await waitFor(() => expect(mockSummary).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="show-segments-panel"]')).toBeNull();
  });
});
