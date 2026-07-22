import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UpdateBanner from './UpdateBanner';

const mockGetStatus = vi.fn();
const mockGetSettings = vi.fn();

vi.mock('../api/updates', () => ({
  getUpdateStatus: () => mockGetStatus(),
  getUpdateCheckSettings: () => mockGetSettings(),
}));

const AVAILABLE = {
  current: { version: '2.74.0' },
  stable: { version: '2.75.0', releaseDate: '2026-07-24', url: 'https://example.invalid', notes: '' },
  edge: null,
  channel: 'stable',
  updateAvailable: true,
};

function renderBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UpdateBanner />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockGetSettings.mockResolvedValue({ enabled: true, channel: 'stable' });
});

describe('UpdateBanner', () => {
  it('renders nothing when up to date', async () => {
    mockGetStatus.mockResolvedValue({ ...AVAILABLE, updateAvailable: false });
    const { container } = renderBanner();
    await new Promise((r) => setTimeout(r, 0));
    expect(container.textContent).toBe('');
  });

  it('shows the banner when an update is available', async () => {
    mockGetStatus.mockResolvedValue(AVAILABLE);
    renderBanner();
    expect(await screen.findByText(/2\.75\.0 is available/i)).toBeTruthy();
  });

  it('dismiss hides it and persists per version', async () => {
    mockGetStatus.mockResolvedValue(AVAILABLE);
    renderBanner();
    await userEvent.click(await screen.findByRole('button', { name: /dismiss/i }));
    expect(screen.queryByText(/2\.75\.0 is available/i)).toBeNull();
    expect(JSON.parse(localStorage.getItem('update-banner-dismissed') ?? '""')).toBe('2.75.0');
  });

  it('does not query when the daily check is disabled', async () => {
    mockGetSettings.mockResolvedValue({ enabled: false, channel: 'stable' });
    renderBanner();
    await new Promise((r) => setTimeout(r, 0));
    expect(mockGetStatus).not.toHaveBeenCalled();
  });
});
