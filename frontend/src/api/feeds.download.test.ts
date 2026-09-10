import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { downloadEpisodeAudio } from './feeds';

describe('downloadEpisodeAudio', () => {
  const assign = vi.fn();
  beforeEach(() => {
    assign.mockReset();
    vi.stubGlobal('location', { ...window.location, assign });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('preflights with HEAD, then streams the attachment by navigation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await downloadEpisodeAudio('show', 'ep1', 'cut');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/feeds/show/episodes/ep1/processed.mp3?download=1');
    expect(fetchMock.mock.calls[0][1].method).toBe('HEAD');
    expect(assign).toHaveBeenCalledWith('/api/v1/feeds/show/episodes/ep1/processed.mp3?download=1');
  });

  it('surfaces the API error and does not navigate on a 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: 'Original audio not retained for this episode', status: 404 }),
      { status: 404 })));
    await expect(downloadEpisodeAudio('show', 'ep1', 'original'))
      .rejects.toThrow('Original audio not retained for this episode');
    expect(assign).not.toHaveBeenCalled();
  });
});
