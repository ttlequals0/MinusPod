import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockApiRequest = vi.fn();

vi.mock('./client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./client')>()),
  apiRequest: (...a: unknown[]) => mockApiRequest(...a),
}));

import {
  getPatternAudio, setPatternAudioEnabled, deletePatternAudio, rebuildPatternAudio,
  getPatternAudioMatches, patternAudioPreviewUrl, describeFingerprintSkip, fetchPatternAudioClip, describePreviewFailure,
  type FingerprintSkippedReason,
} from './patterns';
import { getAudioMatchingStats } from './stats';
import { ApiError } from './client';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('pattern audio calls', () => {
  it('gets the detail', async () => {
    mockApiRequest.mockResolvedValue({ state: 'active' });
    await expect(getPatternAudio(7)).resolves.toEqual({ state: 'active' });
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio');
  });

  it('turns audio on or off with PUT', async () => {
    mockApiRequest.mockResolvedValue({ state: 'off' });
    await setPatternAudioEnabled(7, false);
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio', { method: 'PUT', body: { enabled: false } });
  });

  it('deletes with DELETE', async () => {
    mockApiRequest.mockResolvedValue({ deleted: true });
    await expect(deletePatternAudio(7)).resolves.toBeUndefined();
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio', { method: 'DELETE' });
  });

  it('rebuilds with the slug, episode and span', async () => {
    mockApiRequest.mockResolvedValue({ fingerprintStored: true, state: 'active' });
    const span = { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 10, end: 55 };
    const res = await rebuildPatternAudio(7, span);
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio/rebuild', { method: 'POST', body: span });
    expect(res.fingerprintStored).toBe(true);
  });

  it('pages the matches', async () => {
    mockApiRequest.mockResolvedValue({ matches: [], page: 2, perPage: 10, total: 12 });
    await getPatternAudioMatches(7, { page: 2, perPage: 10 });
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio/matches?page=2&perPage=10');
  });

  it('omits unset paging params', async () => {
    mockApiRequest.mockResolvedValue({ matches: [], page: 1, perPage: 10, total: 0 });
    await getPatternAudioMatches(7);
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio/matches');
  });

  it('gets the audio matching stats', async () => {
    mockApiRequest.mockResolvedValue({ patternsWithAudio: 1 });
    await getAudioMatchingStats({ from: '2026-01-01', podcastSlug: 'example-podcast' });
    expect(mockApiRequest).toHaveBeenCalledWith('/stats/audio-matching?from=2026-01-01&podcastSlug=example-podcast');
  });
});

describe('patternAudioPreviewUrl', () => {
  it('builds the source preview route', () => {
    expect(patternAudioPreviewUrl(7)).toBe('/api/v1/patterns/7/audio/preview');
  });

  it('adds the match id', () => {
    expect(patternAudioPreviewUrl(7, 31)).toBe('/api/v1/patterns/7/audio/preview?matchId=31');
  });

  it('is null in the demo build', async () => {
    vi.resetModules();
    vi.doMock('../demo/flag', () => ({ IS_DEMO: true }));
    const { patternAudioPreviewUrl: demoUrl } = await import('./patterns');
    expect(demoUrl(7)).toBeNull();
    vi.doUnmock('../demo/flag');
  });
});

describe('describeFingerprintSkip', () => {
  const cases: Array<[FingerprintSkippedReason, string]> = [
    ['audio_removed', 'Audio was deleted for this pattern, so it was not added back.'],
    ['fingerprint_exists', 'This pattern already has an audio fingerprint.'],
    ['span_covers_multiple_patterns', 'That selection covers more than one pattern, so no audio fingerprint was stored.'],
    ['original_not_retained', "This episode's original audio is no longer kept."],
    ['segment_too_short', 'That selection is too short for an audio fingerprint.'],
    ['fingerprint_failed', 'Could not make an audio fingerprint from that selection.'],
    ['not_distinctive', 'That audio is not distinctive enough to match reliably.'],
  ];
  it.each(cases)('explains %s', (reason, text) => {
    expect(describeFingerprintSkip(reason)).toBe(text);
  });
});

describe('fetchPatternAudioClip', () => {
  it('fetches the clip once as a blob without retrying a busy server', async () => {
    const blob = new Blob(['x']);
    mockApiRequest.mockResolvedValue(blob);
    const controller = new AbortController();
    await expect(fetchPatternAudioClip(7, 102, controller.signal)).resolves.toBe(blob);
    expect(mockApiRequest).toHaveBeenCalledTimes(1);
    expect(mockApiRequest).toHaveBeenCalledWith('/patterns/7/audio/preview?matchId=102', {
      responseType: 'blob', skipRetry: true, signal: controller.signal,
    });
  });

  it('asks for the source clip without a match id', async () => {
    mockApiRequest.mockResolvedValue(new Blob());
    await fetchPatternAudioClip(7);
    expect(mockApiRequest.mock.calls[0][0]).toBe('/patterns/7/audio/preview');
  });
});

describe('describePreviewFailure', () => {
  it('says a busy server is preparing the audio', () => {
    expect(describePreviewFailure(new ApiError('busy', 503, { error: 'busy' }, 2000, true))).toBe('Audio is being prepared. Try again.');
  });

  it('says when the original is gone', () => {
    expect(describePreviewFailure(new ApiError('gone', 404, { code: 'original_not_retained' }))).toBe("This episode's original audio is no longer kept.");
  });

  it('says when the pattern has no audio', () => {
    expect(describePreviewFailure(new ApiError('none', 404, { code: 'no_audio' }))).toBe('This pattern has no audio fingerprint.');
  });

  it('falls back to a plain retryable message for anything else', () => {
    expect(describePreviewFailure(new ApiError('boom', 500, {}))).toBe('Could not play this audio. Try again.');
    expect(describePreviewFailure(new ApiError('busy', 503, {}))).toBe('Could not play this audio. Try again.');
    expect(describePreviewFailure(new TypeError('offline'))).toBe('Could not play this audio. Try again.');
  });
});
