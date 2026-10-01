import { describe, it, expect } from 'vitest';
import { HOLD_REASON_LABELS, HOLD_REASON_TITLES } from './holdReason';

// Mirrors ALL_HOLD_REASONS in src/config.py.
const ALL_HOLD_REASONS = [
  'max_duration', 'no_cue_evidence', 'no_splice_evidence', 'uncorroborated_tail',
  'reviewer_contradiction', 'reviewer_boundary_conflict', 'reviewer_inconclusive_bounds',
  'reviewer_failed', 'reviewer_reject_conflict', 'estimated_pattern_bounds',
  'verification_miss', 'verification_kept_conflict', 'differential_uncorroborated',
  'large_vad_gap_extension', 'cue_template_unproven', 'cue_low_confidence',
  'no_transcript_evidence',
];

describe('hold reason maps', () => {
  it.each([['labels', HOLD_REASON_LABELS], ['titles', HOLD_REASON_TITLES]])(
    'give every backend hold reason a %s entry', (_name, map) => {
      expect(Object.keys(map).sort()).toEqual([...ALL_HOLD_REASONS].sort());
      for (const reason of ALL_HOLD_REASONS) {
        expect((map as Record<string, string>)[reason]).toBeTruthy();
      }
    });
});

it('labels the transcript-evidence hold', () => {
  expect(HOLD_REASON_LABELS.no_transcript_evidence).toBe('No ad language in transcript');
});
