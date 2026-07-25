/**
 * Component tests for CommunityPatternsSection.tsx (issue #565 follow-up).
 *
 * Covers:
 *   - Renders the per-category breakdown counts from query data.
 *   - Checkboxes reflect the accepted categories from query data.
 *   - Unchecking a category and saving sends the full categories list
 *     (minus the unchecked one) alongside enabled/cron.
 *   - Checking a not-yet-accepted category and saving adds it to the payload.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CommunityPatternsSection from './CommunityPatternsSection';
import type { CommunitySyncSettings } from '../../api/community';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
const mockSyncNow = vi.fn();
const mockPurge = vi.fn();

vi.mock('../../api/community', () => ({
  getCommunitySyncSettings: (...args: unknown[]) => mockGet(...args),
  updateCommunitySyncSettings: (...args: unknown[]) => mockUpdate(...args),
  triggerCommunitySync: (...args: unknown[]) => mockSyncNow(...args),
  purgeAllCommunityPatterns: (...args: unknown[]) => mockPurge(...args),
}));

function makeSettings(overrides: Partial<CommunitySyncSettings> = {}): CommunitySyncSettings {
  return {
    enabled: true,
    cron: '0 3 * * 0',
    lastRun: null,
    lastError: null,
    manifestVersion: null,
    lastSummary: null,
    categories: ['sponsor', 'cross_promo', 'self_promo', 'interaction', 'intro', 'outro', 'recap'],
    categoryBreakdown: {
      sponsor: 312, cross_promo: 24, self_promo: 8, interaction: 0,
      intro: 0, outro: 0, recap: 0,
    },
    ...overrides,
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderSection() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <CommunityPatternsSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue(makeSettings());
  mockUpdate.mockResolvedValue(makeSettings());
});

describe('CommunityPatternsSection', () => {
  it('renders the per-category breakdown counts', async () => {
    renderSection();

    expect(await screen.findByText('(312)')).toBeDefined();
    expect(screen.getByText('(24)')).toBeDefined();
    expect(screen.getByText('(8)')).toBeDefined();
  });

  it('checkboxes reflect the accepted categories from query data', async () => {
    mockGet.mockResolvedValue(makeSettings({ categories: ['sponsor', 'cross_promo'] }));
    renderSection();

    const sponsor = (await screen.findByRole('checkbox', { name: /sponsor \(/i })) as HTMLInputElement;
    const crossPromo = screen.getByRole('checkbox', { name: /cross-promo \(/i }) as HTMLInputElement;
    const selfPromo = screen.getByRole('checkbox', { name: /self-promo \(/i }) as HTMLInputElement;

    expect(sponsor.checked).toBe(true);
    expect(crossPromo.checked).toBe(true);
    expect(selfPromo.checked).toBe(false);
  });

  it('unchecking a category and saving sends the reduced list', async () => {
    mockGet.mockResolvedValue(makeSettings({
      categories: ['sponsor', 'cross_promo', 'self_promo', 'interaction', 'intro', 'outro', 'recap'],
    }));
    const user = userEvent.setup();
    renderSection();

    const crossPromo = await screen.findByRole('checkbox', { name: /cross-promo \(/i });
    await user.click(crossPromo);
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const payload = mockUpdate.mock.calls[0][0];
    expect(payload.categories).not.toContain('cross_promo');
    expect(payload.categories).toEqual(
      expect.arrayContaining(['sponsor', 'self_promo', 'interaction', 'intro', 'outro', 'recap']),
    );
    expect(payload.categories).toHaveLength(6);
  });

  it('checking a not-yet-accepted category and saving adds it to the payload', async () => {
    mockGet.mockResolvedValue(makeSettings({ categories: ['sponsor'] }));
    const user = userEvent.setup();
    renderSection();

    const crossPromo = await screen.findByRole('checkbox', { name: /cross-promo \(/i });
    await user.click(crossPromo);
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const payload = mockUpdate.mock.calls[0][0];
    expect(payload.categories).toEqual(expect.arrayContaining(['sponsor', 'cross_promo']));
    expect(payload.categories).toHaveLength(2);
  });

  it('shows the deactivate-not-delete explanation copy', async () => {
    renderSection();
    expect(
      await screen.findByText(/deactivates its already-synced community patterns/i),
    ).toBeDefined();
  });
});
