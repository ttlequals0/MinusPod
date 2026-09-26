import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DataManagementSection from './DataManagementSection';

const mockDownloadBackup = vi.fn();

vi.mock('../../api/settings', () => ({
  downloadBackup: (...args: unknown[]) => mockDownloadBackup(...args),
  exportOpml: vi.fn(),
  getSettings: vi.fn().mockResolvedValue({}),
}));

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DataManagementSection
          onResetEpisodes={() => {}}
          resetIsPending={false}
          resetData={undefined}
          maxRssBytes={200 * 1024 * 1024}
          onMaxRssBytesChange={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DataManagementSection backup export', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem('settings-section-data-management', 'true');
    mockDownloadBackup.mockResolvedValue(undefined);
  });

  it('requests encryption from the primary backup action', async () => {
    renderSection();

    await userEvent.click(screen.getByRole('button', { name: 'Download Encrypted Backup' }));

    await waitFor(() => expect(mockDownloadBackup).toHaveBeenCalledWith(true));
  });

  it('requires confirmation before requesting a plaintext backup', async () => {
    renderSection();
    const button = screen.getByRole('button', { name: 'Download plaintext database backup' });

    await userEvent.click(button);
    expect(mockDownloadBackup).not.toHaveBeenCalled();
    expect(button.className).toContain('min-h-[44px]');
    await userEvent.click(button);

    await waitFor(() => expect(mockDownloadBackup).toHaveBeenCalledWith(false));
  });

  it('explains how to recover when encrypted export is unavailable', async () => {
    mockDownloadBackup.mockRejectedValue(new Error('backup_encryption_unavailable'));
    renderSection();

    await userEvent.click(screen.getByRole('button', { name: 'Download Encrypted Backup' }));

    expect(await screen.findByText(
      'Encrypted backup unavailable. Set MINUSPOD_MASTER_PASSPHRASE and restart, or download plaintext below.',
    )).toBeDefined();
  });
});
