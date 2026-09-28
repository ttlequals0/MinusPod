import type { HoldReason } from '../api/detections';

export const HOLD_REASON_LABELS: Record<HoldReason, string> = {
  max_duration: 'Over max duration',
  no_cue_evidence: 'No cue evidence',
  no_splice_evidence: 'No splice evidence',
  uncorroborated_tail: 'Uncorroborated tail',
  reviewer_contradiction: 'Reviewer contradiction',
  reviewer_boundary_conflict: 'Reviewer boundary conflict',
  reviewer_inconclusive_bounds: 'Unverified bounds',
  reviewer_failed: 'Reviewer unavailable',
  reviewer_reject_conflict: 'Reviewer reject conflict',
  estimated_pattern_bounds: 'Estimated bounds',
  verification_miss: 'Verification catch',
  verification_kept_conflict: 'Verification kept conflict',
  differential_uncorroborated: 'Differential hold',
  large_vad_gap_extension: 'VAD extension limit',
  cue_template_unproven: 'Unproven cue',
  cue_low_confidence: 'Low-confidence cue',
};
