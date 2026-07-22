import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UpdateStatusPanel from './UpdateStatusPanel';

const mockGetStatus = vi.fn();
const mockGetSettings = vi.fn();
const mockUpdateSettings = vi.fn();

vi.mock('../../api/updates', () => ({
  getUpdateStatus: (...a: unknown[]) => mockGetStatus(...a),
  getUpdateCheckSettings: () => mockGetSettings(),
  updateUpdateCheckSettings: (...a: unknown[]) => mockUpdateSettings(...a),
}));

const STATUS = {
  current: { version: '2.74.0', releaseDate: '2026-07-23' },
  stable: { version: '2.74.0', releaseDate: '2026-07-23', url: 'https://example.invalid', notes: '' },
  edge: { version: '2.75.0', releaseDate: '2026-07-24', url: 'https://example.invalid', notes: '' },
  channel: 'stable',
  updateAvailable: false,
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UpdateStatusPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetStatus.mockResolvedValue(STATUS);
  mockGetSettings.mockResolvedValue({ enabled: true, channel: 'stable' });
  mockUpdateSettings.mockResolvedValue({ enabled: true, channel: 'edge' });
});

describe('UpdateStatusPanel', () => {
  it('shows the release date and up-to-date state', async () => {
    renderPanel();
    expect(await screen.findByText(/released 2026-07-23/i)).toBeTruthy();
    expect(screen.getByText(/up to date/i)).toBeTruthy();
  });

  it('shows the available update on the selected channel', async () => {
    mockGetStatus.mockResolvedValue({ ...STATUS, channel: 'edge', updateAvailable: true });
    renderPanel();
    expect(await screen.findByText(/2\.75\.0 is available/i)).toBeTruthy();
  });

  it('check for updates button calls the refresh endpoint', async () => {
    renderPanel();
    await screen.findByText(/up to date/i);
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }));
    await waitFor(() => expect(mockGetStatus).toHaveBeenCalledWith(true));
  });

  it('channel select saves the setting', async () => {
    renderPanel();
    const select = await screen.findByLabelText(/channel/i);
    await userEvent.selectOptions(select, 'edge');
    await waitFor(() =>
      expect(mockUpdateSettings).toHaveBeenCalledWith({ channel: 'edge' }));
  });

  it('toggle saves enabled setting', async () => {
    renderPanel();
    const toggle = await screen.findByRole('switch', { name: /check for updates daily/i });
    await userEvent.click(toggle);
    await waitFor(() =>
      expect(mockUpdateSettings).toHaveBeenCalledWith({ enabled: false }));
  });

  it('links to the releases page', async () => {
    renderPanel();
    const link = await screen.findByRole('link', { name: /changelog/i });
    expect(link.getAttribute('href')).toBe('https://github.com/ttlequals0/MinusPod/releases');
  });

  it('shows an error message when the check fails', async () => {
    renderPanel();
    await screen.findByText(/up to date/i);
    mockGetStatus.mockRejectedValueOnce(new Error('Update check failed; GitHub may be unreachable'));
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }));
    expect(await screen.findByText(/update check failed; github may be unreachable/i)).toBeTruthy();
  });
});
