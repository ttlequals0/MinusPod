import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import YouTubeSection from './YouTubeSection';
import type { YouTubeSettings } from '../../api/youtube';
import { hasUnsavedChanges } from '../../hooks/useUnsavedChanges';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
const mockTestKey = vi.fn();
const mockSaveCookies = vi.fn();
const mockClearCookies = vi.fn();
const mockUpdateNow = vi.fn();
const mockReset = vi.fn();
vi.mock('../../api/youtube', () => ({
  getYouTubeSettings: (...a: unknown[]) => mockGet(...a),
  updateYouTubeSettings: (...a: unknown[]) => mockUpdate(...a),
  testYouTubeKey: (...a: unknown[]) => mockTestKey(...a),
  saveYouTubeCookies: (...a: unknown[]) => mockSaveCookies(...a),
  clearYouTubeCookies: (...a: unknown[]) => mockClearCookies(...a),
  updateYtDlpNow: (...a: unknown[]) => mockUpdateNow(...a),
  resetYouTubeSettings: (...a: unknown[]) => mockReset(...a),
}));

const base: YouTubeSettings = {
  enabled: false, apiKeyConfigured: false, processDelayMinutes: 60, sbProbeAttempts: 6,
  sbProbeIntervalMinutes: 30, sbWatchIntervalHours: 6, sbWatchMaxDays: 7, llmFallback: true,
  captionsAsTranscript: true, hidePrivate: true, transcribeFallback: false, ytdlpExtraArgs: '', proxyUrl: '',
  ytdlp: { autoUpdate: true, version: '2026.10.26', lastUpdateAt: null, lastCheckAt: null, lastError: null,
           bakedVersion: '2026.10.20' },
  cookies: { set: false, lines: 0, updatedAt: null },
  bounds: { processDelayMinutes: [0, 1440], sbProbeAttempts: [0, 48], sbProbeIntervalMinutes: [5, 1440],
            sbWatchIntervalHours: [1, 48], sbWatchMaxDays: [1, 60] },
};

function renderSection(data: YouTubeSettings = base) {
  mockGet.mockResolvedValue(data);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><YouTubeSection /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem('settings-section-youtube', 'true');
});

describe('YouTubeSection', () => {
  it('renders the settings and saves the toggle', async () => {
    mockUpdate.mockResolvedValue({ ...base, enabled: true });
    renderSection();
    expect((await screen.findByRole('switch', { name: /enable youtube feeds/i })).getAttribute('aria-checked')).toBe('false');
    expect((screen.getByLabelText(/processing delay/i) as HTMLInputElement).value).toBe('60');
    expect(screen.getByText(/yt-dlp 2026\.10\.26/)).toBeDefined();
    await userEvent.click(screen.getByRole('switch', { name: /enable youtube feeds/i }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ enabled: true }));
  });

  it('saves the transcription fallback toggle', async () => {
    mockUpdate.mockResolvedValue({ ...base, transcribeFallback: true });
    renderSection();
    const toggle = await screen.findByRole('switch', { name: /transcribe when captions are missing/i });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    await userEvent.click(toggle);
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ transcribeFallback: true }));
  });

  it('saves the hide private toggle', async () => {
    mockUpdate.mockResolvedValue({ ...base, hidePrivate: false });
    renderSection();
    const toggle = await screen.findByRole('switch', { name: /Hide private and deleted episodes/i });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    await userEvent.click(toggle);
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ hidePrivate: false }));
  });

  it('saves the extra yt-dlp arguments on blur', async () => {
    mockUpdate.mockResolvedValue({ ...base, ytdlpExtraArgs: '--limit-rate 2M' });
    renderSection();
    await userEvent.type(await screen.findByLabelText(/extra yt-dlp arguments/i), '--limit-rate 2M');
    await userEvent.tab();
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ ytdlpExtraArgs: '--limit-rate 2M' }));
  });

  it('saves the proxy URL on blur', async () => {
    mockUpdate.mockResolvedValue({ ...base, proxyUrl: 'socks5h://proxy:1080' });
    renderSection();
    await userEvent.type(await screen.findByLabelText(/proxy url/i), 'socks5h://proxy:1080');
    await userEvent.tab();
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ proxyUrl: 'socks5h://proxy:1080' }));
  });

  it('shows the server message when the extra arguments are rejected', async () => {
    mockUpdate.mockRejectedValue(new Error('extra yt-dlp arguments: --exec is not allowed'));
    renderSection();
    await userEvent.type(await screen.findByLabelText(/extra yt-dlp arguments/i), '--exec x');
    await userEvent.tab();
    expect(await screen.findByText(/--exec is not allowed/i)).toBeDefined();
    const input = screen.getByLabelText(/extra yt-dlp arguments/i);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(input.getAttribute('aria-describedby')).toContain('yt-extra-args-error');
    expect((input as HTMLInputElement).value).toBe('--exec x');
    expect(hasUnsavedChanges()).toBe(true);
  });

  it('tests a typed key and reports the result', async () => {
    mockTestKey.mockResolvedValue({ ok: true, channelTitle: 'YouTube' });
    renderSection();
    await userEvent.type(await screen.findByLabelText(/data api key/i), 'AIzaSyTEST');
    await userEvent.click(screen.getByRole('button', { name: /test key/i }));
    expect(await screen.findByText(/key works/i)).toBeDefined();
    expect(mockTestKey).toHaveBeenCalledWith('AIzaSyTEST');
  });

  it('shows a rejected key save under the field', async () => {
    mockUpdate.mockRejectedValue(new Error('A master passphrase is required to store the key'));
    renderSection();
    await userEvent.type(await screen.findByLabelText(/data api key/i), 'AIzaSyTEST');
    await userEvent.click(screen.getByRole('button', { name: /save key/i }));
    expect(await screen.findByText(/master passphrase is required/i)).toBeDefined();
    expect(mockUpdate).toHaveBeenCalledWith({ apiKey: 'AIzaSyTEST' });
  });

  it('saves cookies and shows the status', async () => {
    mockSaveCookies.mockResolvedValue({ set: true, lines: 12, updatedAt: '2026-10-29T10:00:00Z' });
    renderSection();
    await userEvent.type(await screen.findByLabelText(/cookies/i), '# Netscape HTTP Cookie File');
    await userEvent.click(screen.getByRole('button', { name: /save cookies/i }));
    expect(await screen.findByText(/12 lines/)).toBeDefined();
  });

  it('runs the yt-dlp update on demand', async () => {
    mockUpdateNow.mockResolvedValue({ updated: true, version: '2026.10.27', error: null });
    renderSection();
    await userEvent.click(await screen.findByRole('button', { name: /update now/i }));
    expect(await screen.findByText(/updated to 2026\.10\.27/i)).toBeDefined();
  });

  it('resets to defaults after confirming and refreshes', async () => {
    mockReset.mockResolvedValue(base);
    renderSection({ ...base, processDelayMinutes: 5 });
    await userEvent.click(await screen.findByRole('button', { name: 'Reset to defaults' }));
    expect(screen.getByText(/including the daily yt-dlp update toggle\? The feature switch, Data API key and cookies file are kept/)).toBeDefined();
    expect(mockReset).not.toHaveBeenCalled();
    const getCalls = mockGet.mock.calls.length;
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reset to defaults' }));
    await waitFor(() => expect(mockReset).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockGet.mock.calls.length).toBeGreaterThan(getCalls));
  });

  it('replaces failed loading with retry and recovers', async () => {
    mockGet.mockRejectedValueOnce(new Error('down'));
    renderSection();
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load YouTube settings.');
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('switch', { name: /enable youtube feeds/i })).toBeTruthy();
  });

  it('tracks unsaved keys and cookies and blocks concurrent cookie changes', async () => {
    mockSaveCookies.mockImplementation(() => new Promise(() => {}));
    const view = renderSection({ ...base, cookies: { set: true, lines: 12, updatedAt: null } });
    await userEvent.type(await screen.findByLabelText(/data api key/i), 'AIzaSyTEST');
    expect(hasUnsavedChanges()).toBe(true);
    await userEvent.clear(screen.getByLabelText(/data api key/i));
    expect(hasUnsavedChanges()).toBe(false);
    await userEvent.type(screen.getByLabelText('Cookies', { exact: true }), '# Netscape HTTP Cookie File');
    await userEvent.click(screen.getByRole('button', { name: 'Save cookies' }));
    expect((screen.getByRole('button', { name: 'Clear cookies' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('Cookies', { exact: true }) as HTMLTextAreaElement).disabled).toBe(true);
    expect(hasUnsavedChanges()).toBe(true);
    view.unmount();
    expect(hasUnsavedChanges()).toBe(false);
  });
});
