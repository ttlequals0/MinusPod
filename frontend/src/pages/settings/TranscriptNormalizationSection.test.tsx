/**
 * Tests for the Transcript Normalization settings section: rule list with
 * counts, add/edit/delete wiring, and the failed-load state.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TranscriptNormalizationSection from './TranscriptNormalizationSection';
import * as sponsorsApi from '../../api/sponsors';
import type { SponsorNormalization } from '../../api/types';

vi.mock('../../api/sponsors', () => ({
  getNormalizations: vi.fn(),
  deleteNormalization: vi.fn(),
}));

const mocked = vi.mocked(sponsorsApi);

const rule = (id: number): SponsorNormalization => ({
  id,
  terms: '(?i)acme\\s+corp',
  canonical: 'acme',
  category: 'sponsor',
  is_active: true,
  created_at: '2026-08-30T00:00:00Z',
});

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TranscriptNormalizationSection />
    </QueryClientProvider>
  );
}

describe('TranscriptNormalizationSection', () => {
  it('renders the rule count and table rows', async () => {
    mocked.getNormalizations.mockResolvedValue([rule(1), rule(2)]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('2 rules')).toBeTruthy();
    });
    // Mobile and desktop layouts both render (CSS is not loaded in tests).
    expect(screen.getAllByText('(?i)acme\\s+corp').length).toBeGreaterThan(0);
  });

  it('opens the add modal from the header button', async () => {
    mocked.getNormalizations.mockResolvedValue([]);
    renderSection();
    const user = userEvent.setup();
    await user.click(await screen.findByText('+ Add Normalization'));
    expect(screen.getByText('Add Normalization')).toBeTruthy();
  });

  it('confirms before deleting a rule', async () => {
    mocked.getNormalizations.mockResolvedValue([rule(7)]);
    mocked.deleteNormalization.mockResolvedValue(undefined);
    renderSection();
    const user = userEvent.setup();
    // The card and (CSS-unstyled in tests) table layouts both render a Delete.
    await user.click((await screen.findAllByRole('button', { name: 'Delete' }))[0]);
    expect(screen.getByText('Delete normalization?')).toBeTruthy();
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => {
      expect(mocked.deleteNormalization).toHaveBeenCalledWith(7);
    });
  });

  it('shows the failed-load state', async () => {
    mocked.getNormalizations.mockRejectedValue(new Error('boom'));
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Failed to load normalizations')).toBeTruthy();
    });
  });
});
