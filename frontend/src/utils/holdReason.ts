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
  no_transcript_evidence: 'No ad language in transcript',
};

// Tooltip text for each hold reason.
export const HOLD_REASON_TITLES: Record<HoldReason, string> = {
  max_duration: "Exceeds the feed's max ad duration",
  no_cue_evidence: 'No audio-cue evidence',
  no_splice_evidence: 'No splice artifact found at either edge',
  uncorroborated_tail: 'Trailing ad with no audio evidence to back it',
  reviewer_contradiction: 'The reviewer disagreed with the detected boundaries',
  reviewer_boundary_conflict: 'The reviewer proposed a boundary that crosses protected ad evidence',
  reviewer_inconclusive_bounds: 'The reviewer could not verify both cut boundaries',
  reviewer_failed: 'The reviewer could not be reached and no independent evidence backs the bounds',
  reviewer_reject_conflict: 'The reviewer rejected a span that carries measured ad evidence',
  estimated_pattern_bounds: 'Estimated pattern remainder outside the verified ad bounds',
  verification_miss: 'A standalone catch from the verification pass, held for a second opinion',
  verification_kept_conflict: 'A verification-pass catch runs into audio the category settings keep',
  differential_uncorroborated: 'Audio differs across fetches with no corroborating signal',
  large_vad_gap_extension: 'Untranscribed audio exceeded the safe adjacency-only extension limit',
  cue_template_unproven: "This cue template hasn't cut a confirmed ad yet",
  cue_low_confidence: 'The cue match fell below the cut-confidence threshold',
  no_transcript_evidence: "No sponsor, link, promo code or ad phrase in this span's transcript",
};
