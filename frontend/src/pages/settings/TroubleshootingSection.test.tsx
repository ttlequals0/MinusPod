import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TroubleshootingSection from './TroubleshootingSection';

const mockDownloadConfig = vi.fn();
const mockDownloadDiagnostics = vi.fn();

vi.mock('../../api/settings', () => ({
  downloadConfig: (...args: unknown[]) => mockDownloadConfig(...args),
  downloadDiagnostics: (...args: unknown[]) => mockDownloadDiagnostics(...args),
}));

describe('TroubleshootingSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem('settings-section-troubleshooting', 'true');
    mockDownloadConfig.mockResolvedValue(undefined);
    mockDownloadDiagnostics.mockResolvedValue(undefined);
  });

  it('keeps the export cards in a responsive two-column grid', () => {
    render(<TroubleshootingSection />);

    expect(screen.getByRole('heading', { name: 'Troubleshooting' })).toBeDefined();
    expect(screen.getByText('Configuration Export').parentElement?.parentElement?.parentElement?.parentElement?.className)
      .toContain('sm:grid-cols-2');
  });

  it('downloads the redacted configuration once and shows the busy state', async () => {
    let resolveDownload: () => void = () => {};
    mockDownloadConfig.mockReturnValue(new Promise<void>((resolve) => { resolveDownload = resolve; }));
    render(<TroubleshootingSection />);
    const button = screen.getByRole('button', { name: 'Download Configuration' });

    await userEvent.click(button);

    expect(mockDownloadConfig).toHaveBeenCalledOnce();
    expect(button.textContent).toBe('Preparing download');
    expect((button as HTMLButtonElement).disabled).toBe(true);

    resolveDownload();
    await waitFor(() => expect(button.textContent).toBe('Download Configuration'));
  });

  it('sends the selected UTC diagnostic time range', async () => {
    render(<TroubleshootingSection />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Diagnostic time range' }), '6');
    await userEvent.click(screen.getByRole('button', { name: 'Download Diagnostics' }));

    await waitFor(() => expect(mockDownloadDiagnostics).toHaveBeenCalledOnce());
    const [start, end] = mockDownloadDiagnostics.mock.calls[0] as [string, string];
    expect(new Date(end).getTime() - new Date(start).getTime()).toBe(6 * 60 * 60 * 1000);
  });

  it('shows export errors', async () => {
    mockDownloadConfig.mockRejectedValue(new Error('config failed'));
    render(<TroubleshootingSection />);

    await userEvent.click(screen.getByRole('button', { name: 'Download Configuration' }));

    expect(await screen.findByText('config failed')).toBeDefined();
  });

  it('shows diagnostic export errors', async () => {
    mockDownloadDiagnostics.mockRejectedValue(new Error('diagnostics failed'));
    render(<TroubleshootingSection />);

    await userEvent.click(screen.getByRole('button', { name: 'Download Diagnostics' }));

    expect(await screen.findByText('diagnostics failed')).toBeDefined();
  });
});
