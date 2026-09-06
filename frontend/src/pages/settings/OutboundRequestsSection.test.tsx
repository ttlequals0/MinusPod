/**
 * Tests for the Outbound Requests section: editing the two User-Agent
 * strings, and resetting one back to its default.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import OutboundRequestsSection from './OutboundRequestsSection';
import * as settingsApi from '../../api/settings';

vi.mock('../../api/settings', () => ({
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
}));

const mocked = vi.mocked(settingsApi);

const DEFAULT_DOWNLOAD_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/153.0.0.0';

function makeSettings(overrides = {}) {
  return {
    downloadUserAgent: { value: DEFAULT_DOWNLOAD_UA, isDefault: true },
    feedUserAgent: { value: 'PodcastAdRemover/1.0', isDefault: true },
    ...overrides,
  };
}

/** The fields render before the settings query resolves, so every test waits
 *  for a loaded value rather than for the input to exist. */
async function findLoadedField(label: string, value: string) {
  const field = await screen.findByLabelText(label);
  await waitFor(() => expect((field as HTMLInputElement).value).toBe(value));
  return field;
}

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OutboundRequestsSection />
    </QueryClientProvider>,
  );
}

describe('OutboundRequestsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mocked.getSettings.mockResolvedValue(makeSettings() as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mocked.updateSettings.mockResolvedValue({} as any);
  });

  it('shows the stored User-Agent for each field', async () => {
    renderSection();
    await findLoadedField('Audio, artwork, and chapters', DEFAULT_DOWNLOAD_UA);
    await findLoadedField('RSS feeds', 'PodcastAdRemover/1.0');
  });

  it('saves only the edited field', async () => {
    renderSection();
    const download = await findLoadedField(
      'Audio, artwork, and chapters', DEFAULT_DOWNLOAD_UA);
    fireEvent.change(download, { target: { value: 'CustomAgent/9.9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(mocked.updateSettings).toHaveBeenCalledWith({
        downloadUserAgent: 'CustomAgent/9.9',
      });
    });
  });

  it('keeps Save disabled until a field actually changes', async () => {
    renderSection();
    await findLoadedField('RSS feeds', 'PodcastAdRemover/1.0');
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
    fireEvent.change(screen.getByLabelText('RSS feeds'), {
      target: { value: 'MyClient/2.0' },
    });
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', false);
  });

  it('sends an empty string to reset a customized field', async () => {
    mocked.getSettings.mockResolvedValue(makeSettings({
      downloadUserAgent: { value: 'CustomAgent/9.9', isDefault: false },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any);
    renderSection();
    await findLoadedField('Audio, artwork, and chapters', 'CustomAgent/9.9');
    const reset = screen.getByRole('button', {
      name: 'Reset Audio, artwork, and chapters User-Agent',
    });
    // Two-click confirm: the first click only arms the button.
    fireEvent.click(reset);
    fireEvent.click(reset);
    await waitFor(() => {
      expect(mocked.updateSettings).toHaveBeenCalledWith({ downloadUserAgent: '' });
    });
  });

  it('disables reset while a field is already at its default', async () => {
    renderSection();
    await findLoadedField('RSS feeds', 'PodcastAdRemover/1.0');
    expect(screen.getByRole('button', { name: 'Reset RSS feeds User-Agent' }))
      .toHaveProperty('disabled', true);
  });

  it('keeps the other field\'s unsaved edit when one field is reset', async () => {
    mocked.getSettings.mockResolvedValue(makeSettings({
      downloadUserAgent: { value: 'CustomAgent/9.9', isDefault: false },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }) as any);
    renderSection();
    await findLoadedField('Audio, artwork, and chapters', 'CustomAgent/9.9');
    fireEvent.change(screen.getByLabelText('RSS feeds'), {
      target: { value: 'MyClient/2.0' },
    });

    const reset = screen.getByRole('button', {
      name: 'Reset Audio, artwork, and chapters User-Agent',
    });
    fireEvent.click(reset);
    fireEvent.click(reset);

    await waitFor(() => {
      expect(mocked.updateSettings).toHaveBeenCalledWith({ downloadUserAgent: '' });
    });
    expect((screen.getByLabelText('RSS feeds') as HTMLInputElement).value)
      .toBe('MyClient/2.0');
  });

  it('surfaces a save failure instead of clearing the draft', async () => {
    mocked.updateSettings.mockRejectedValueOnce(
      new Error('downloadUserAgent must be printable ASCII on a single line'));
    renderSection();
    const download = await findLoadedField(
      'Audio, artwork, and chapters', DEFAULT_DOWNLOAD_UA);
    fireEvent.change(download, { target: { value: 'bad' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(
      'downloadUserAgent must be printable ASCII on a single line')).toBeDefined();
    expect((screen.getByLabelText('Audio, artwork, and chapters') as HTMLInputElement).value)
      .toBe('bad');
  });
});
