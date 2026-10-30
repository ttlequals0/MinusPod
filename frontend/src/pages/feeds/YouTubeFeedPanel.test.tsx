import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import YouTubeFeedPanel from './YouTubeFeedPanel';
import type { Feed } from '../../api/types';

vi.mock('../../components/CollapsibleSection', () => ({
  default: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div><h2>{title}</h2>{children}</div>
  ),
}));

const mockUpdateFeed = vi.fn();
const mockUploadFeedArtwork = vi.fn();
vi.mock('../../api/feeds', () => ({
  updateFeed: (...args: unknown[]) => mockUpdateFeed(...args),
  uploadFeedArtwork: (...args: unknown[]) => mockUploadFeedArtwork(...args),
}));

let mockEnabled = true;
vi.mock('../../api/youtube', () => ({
  getYouTubeSettings: () => Promise.resolve({
    enabled: mockEnabled,
    processDelayMinutes: 60, sbProbeAttempts: 6, sbProbeIntervalMinutes: 30,
    llmFallback: true, captionsAsTranscript: false,
    bounds: { processDelayMinutes: [0, 1440], sbProbeAttempts: [1, 48], sbProbeIntervalMinutes: [1, 720] },
  }),
}));

const feed = {
  slug: 'yt', feedType: 'youtube', youtubeSourceKind: 'playlist',
  youtubeSourceUrl: 'https://www.youtube.com/playlist?list=PL1',
  youtubeIncludeShorts: null, ytProcessDelayMinutesOverride: null,
  ytProbeAttemptsOverride: null, ytProbeIntervalMinutesOverride: null,
  ytLlmFallbackOverride: null, ytLlmOnTopOverride: null, ytCaptionsOverride: null,
} as unknown as Feed;

function renderPanel(overrides: Partial<Feed> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <YouTubeFeedPanel feed={{ ...feed, ...overrides }} slug="yt" />
    </QueryClientProvider>,
  );
}

describe('YouTubeFeedPanel', () => {
  beforeEach(() => {
    mockEnabled = true;
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
    const select = screen.getByLabelText('LLM on top of SponsorBlock') as HTMLSelectElement;
    expect(select.selectedOptions[0].textContent).toBe('Use global (off)');
    await user.selectOptions(select, 'On');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytLlmOnTopOverride: true }));
  });

  it('shows the inherited global from settings', async () => {
    renderPanel();
    const select = screen.getByLabelText('LLM fallback') as HTMLSelectElement;
    await waitFor(() => expect(select.selectedOptions[0].textContent).toBe('Use global (on)'));
  });

  it('commits a custom delay on blur', async () => {
    const user = userEvent.setup();
    renderPanel();
    const mode = screen.getByLabelText('Delay mode') as HTMLSelectElement;
    await waitFor(() => expect(mode.selectedOptions[0].textContent).toBe('Use global (60)'));
    await user.selectOptions(mode, 'Custom');
    const input = screen.getByLabelText('Delay');
    await user.clear(input);
    await user.type(input, '5');
    await user.tab();
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytProcessDelayMinutesOverride: 5 }));
  });

  it('does not write the global when the field is focused and blurred untouched', async () => {
    const user = userEvent.setup();
    renderPanel();
    const mode = screen.getByLabelText('Delay mode') as HTMLSelectElement;
    await waitFor(() => expect(mode.selectedOptions[0].textContent).toBe('Use global (60)'));
    await user.selectOptions(mode, 'Custom');
    await user.click(screen.getByLabelText('Delay'));
    await user.tab();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('does not write when the typed value equals the saved override', async () => {
    const user = userEvent.setup();
    renderPanel({ ytProcessDelayMinutesOverride: 5 });
    const input = screen.getByLabelText('Delay');
    await user.clear(input);
    await user.type(input, '5');
    await user.tab();
    expect(mockUpdateFeed).not.toHaveBeenCalled();
  });

  it('sends null when switching back to the global', async () => {
    const user = userEvent.setup();
    renderPanel({ ytProcessDelayMinutesOverride: 5 });
    await user.selectOptions(screen.getByLabelText('Delay mode'), 'global');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { ytProcessDelayMinutesOverride: null }));
  });

  it('sends false for Off on a boolean row', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.selectOptions(screen.getByLabelText('Include Shorts'), 'Off');
    await waitFor(() => expect(mockUpdateFeed).toHaveBeenCalledWith('yt', { youtubeIncludeShorts: false }));
  });
});
