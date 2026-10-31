import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import YouTubeFeedPanel from './YouTubeFeedPanel';
import type { Feed } from '../../api/types';
import { hasUnsavedChanges } from '../../hooks/useUnsavedChanges';

vi.mock('../../components/CollapsibleSection', () => ({
  default: ({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) => (
    <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}{children}</div>
  ),
}));

const mockUpdateFeed = vi.fn();
const mockUploadFeedArtwork = vi.fn();
vi.mock('../../api/feeds', () => ({
  updateFeed: (...args: unknown[]) => mockUpdateFeed(...args),
  uploadFeedArtwork: (...args: unknown[]) => mockUploadFeedArtwork(...args),
}));

let mockEnabled = true;
const mockGetSettings = vi.fn();
vi.mock('../../api/youtube', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/youtube')>()),
  getYouTubeSettings: () => mockGetSettings().then(() => ({
    enabled: mockEnabled,
    processDelayMinutes: 60, sbProbeAttempts: 6, sbProbeIntervalMinutes: 30,
    llmFallback: true, captionsAsTranscript: false, hidePrivate: true, transcribeFallback: true,
    bounds: { processDelayMinutes: [0, 1440], sbProbeAttempts: [1, 48], sbProbeIntervalMinutes: [1, 720] },
  })),
}));

const feed = {
  slug: 'yt', feedType: 'youtube', youtubeSourceKind: 'playlist',
  youtubeSourceUrl: 'https://www.youtube.com/playlist?list=PL1',
  youtubeIncludeShorts: null, ytProcessDelayMinutesOverride: null,
  ytProbeAttemptsOverride: null, ytProbeIntervalMinutesOverride: null,
  ytLlmFallbackOverride: null, ytLlmOnTopOverride: null, ytCaptionsOverride: null, ytHidePrivateOverride: null,
  ytTranscribeOverride: null, author: 'Old', explicit: false, categories: [],
} as unknown as Feed;

function renderPanel(overrides: Partial<Feed> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <YouTubeFeedPanel feed={{ ...feed, ...overrides }} slug="yt" />
    </QueryClientProvider>,
  );
  return { ...view, rerenderFeed: (next: Feed) => view.rerender(
    <QueryClientProvider client={client}><YouTubeFeedPanel feed={next} slug="yt" /></QueryClientProvider>,
  ) };
}

describe('YouTubeFeedPanel', () => {
  beforeEach(() => {
    mockEnabled = true;
    mockGetSettings.mockReset();
    mockGetSettings.mockResolvedValue(undefined);
    mockUpdateFeed.mockReset();
    mockUpdateFeed.mockResolvedValue({});
  });

  it('shows a notice only when YouTube feeds are turned off globally', async () => {
    mockEnabled = false;
    renderPanel();
    expect(await screen.findByText('YouTube feeds are turned off in Settings.')).toBeTruthy();
  });

  it('shows no notice when YouTube feeds are on', async () => {
    renderPanel();
    await screen.findByText('Delay');
    expect(screen.queryByText('YouTube feeds are turned off in Settings.')).toBeNull();
  });

  it('labels a single-video feed as Video', async () => {
    renderPanel({ youtubeSourceKind: 'video' });
    await screen.findByText('Delay');
    expect(screen.getByText('Video')).toBeTruthy();
  });

  it('renders the section subtitle', () => {
    renderPanel();
    expect(screen.getByText('SponsorBlock, captions, transcription and listing options for this source.')).toBeTruthy();
  });

  it('links to the source on YouTube', () => {
    renderPanel();
    const link = screen.getByRole('link', { name: 'Open on YouTube' });
    expect(link.getAttribute('href')).toBe('https://www.youtube.com/playlist?list=PL1');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(screen.getByText('Playlist')).toBeTruthy();
  });

  it('uploads feed artwork', async () => {
    const user = userEvent.setup();
    mockUploadFeedArtwork.mockResolvedValue({});
    renderPanel();
    const file = new File(['x'], 'cover.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText('Artwork'), file);
    await waitFor(() => expect(mockUploadFeedArtwork).toHaveBeenCalledWith('yt', file));
  });

  it('sends a boolean override and null to inherit', async () => {
    const user = userEvent.setup();
    renderPanel();
    const select = await screen.findByLabelText('Pass 1 detection') as HTMLSelectElement;
    expect(select.selectedOptions[0].textContent).toBe('Use global (off)');
    await user.selectOptions(select, 'On');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytLlmOnTopOverride: true }));
  });

  it('shows the inherited global from settings', async () => {
    renderPanel();
    const select = await screen.findByLabelText('LLM fallback') as HTMLSelectElement;
    await waitFor(() => expect(select.selectedOptions[0].textContent).toBe('Use global (on)'));
  });

  it('commits a custom delay on blur', async () => {
    const user = userEvent.setup();
    renderPanel();
    const mode = await screen.findByLabelText('Delay mode') as HTMLSelectElement;
    await waitFor(() => expect(mode.selectedOptions[0].textContent).toBe('Use global (60 minutes)'));
    await user.selectOptions(mode, 'Custom');
    const input = screen.getByRole('spinbutton', { name: 'Delay' });
    await user.clear(input);
    await user.type(input, '5');
    await user.tab();
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytProcessDelayMinutesOverride: 5 }));
  });

  it('does not write the global when the field is focused and blurred untouched', async () => {
    const user = userEvent.setup();
    renderPanel();
    const mode = await screen.findByLabelText('Delay mode') as HTMLSelectElement;
    await waitFor(() => expect(mode.selectedOptions[0].textContent).toBe('Use global (60 minutes)'));
    await user.selectOptions(mode, 'Custom');
    await user.click(screen.getByRole('spinbutton', { name: 'Delay' }));
    await user.tab();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('does not write when the typed value equals the saved override', async () => {
    const user = userEvent.setup();
    renderPanel({ ytProcessDelayMinutesOverride: 5 });
    const input = await screen.findByRole('spinbutton', { name: 'Delay' });
    await user.clear(input);
    await user.type(input, '5');
    await user.tab();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('sends null when switching back to the global', async () => {
    const user = userEvent.setup();
    renderPanel({ ytProcessDelayMinutesOverride: 5 });
    await user.selectOptions(await screen.findByLabelText('Delay mode'), 'global');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytProcessDelayMinutesOverride: null }));
  });

  it('shows the transcription override and sends it', async () => {
    const user = userEvent.setup();
    renderPanel();
    const select = await screen.findByLabelText('Transcribe when captions are missing') as HTMLSelectElement;
    await waitFor(() => expect(select.selectedOptions[0].textContent).toBe('Use global (on)'));
    await user.selectOptions(select, 'Off');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytTranscribeOverride: false }));
  });

  it('shows the hide private override and sends it', async () => {
    const user = userEvent.setup();
    renderPanel();
    const select = await screen.findByLabelText('Hide private and deleted episodes') as HTMLSelectElement;
    await waitFor(() => expect(select.selectedOptions[0].textContent).toBe('Use global (on)'));
    await user.selectOptions(select, 'Off');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytHidePrivateOverride: false }));
  });

  it('saves author, explicit and categories from the metadata form', async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(screen.queryByLabelText('Title')).toBeNull();
    const author = screen.getByLabelText('Author');
    await user.clear(author);
    await user.type(author, 'New Author');
    await user.click(screen.getByLabelText('Categories'));
    await user.type(screen.getByLabelText('Categories'), 'Technology, Business');
    await user.click(screen.getByRole('button', { name: 'Save metadata' }));
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', {
      author: 'New Author', explicit: false, categories: ['Technology', 'Business'],
    }));
  });

  it('sends false for Off on a boolean row', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.selectOptions(await screen.findByLabelText('Include Shorts'), 'Off');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { youtubeIncludeShorts: false }));
  });

  it('does not claim globals are off when the request fails', async () => {
    mockGetSettings.mockRejectedValueOnce(new Error('down'));
    renderPanel();
    expect(screen.queryByLabelText('LLM fallback')).toBeNull();
    expect((await screen.findByRole('alert')).textContent).toContain('Could not load global YouTube settings.');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect((await screen.findByLabelText('LLM fallback') as HTMLSelectElement).selectedOptions[0].textContent).toBe('Use global (on)');
  });

  it('preserves metadata drafts when unrelated feed settings refresh', async () => {
    const view = renderPanel();
    const author = screen.getByLabelText('Author') as HTMLInputElement;
    await userEvent.clear(author);
    await userEvent.type(author, 'New Author');
    view.rerenderFeed({ ...feed, ytLlmFallbackOverride: true });
    expect(author.value).toBe('New Author');
    expect(hasUnsavedChanges()).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(author.value).toBe('Old');
    expect(hasUnsavedChanges()).toBe(false);
  });

  it('validates a custom number and saves an explicit override equal to the global', async () => {
    renderPanel();
    await userEvent.selectOptions(await screen.findByLabelText('Delay mode'), 'custom');
    const input = screen.getByRole('spinbutton', { name: 'Delay' });
    await userEvent.clear(input);
    await userEvent.type(input, '-1');
    await userEvent.tab();
    expect((await screen.findByRole('alert')).textContent).toContain('Enter a whole number');
    expect(mockUpdateFeed).not.toHaveBeenCalled();
    await userEvent.clear(input);
    await userEvent.type(input, '60');
    await userEvent.tab();
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytProcessDelayMinutesOverride: 60 }));
  });
});
