import { describe, expect, it } from 'vitest';
import { audioSummaryLabel } from './patternAudio';

const summary = (state: 'active' | 'off' | 'none', available: boolean, matchCount: number, meanSimilarity: number | null) =>
  ({ state, available, matchCount, meanSimilarity });

describe('audioSummaryLabel', () => {
  it('labels every state', () => {
    expect(audioSummaryLabel(undefined)).toBe('No audio');
    expect(audioSummaryLabel(summary('none', false, 0, null))).toBe('No audio');
    expect(audioSummaryLabel(summary('off', true, 4, 0.9))).toBe('Audio off');
    expect(audioSummaryLabel(summary('active', false, 0, null))).toBe('No source audio');
    expect(audioSummaryLabel(summary('active', true, 0, null))).toBe('No matches yet');
    expect(audioSummaryLabel(summary('active', true, 4, 0.912))).toBe('91% match');
  });
});
