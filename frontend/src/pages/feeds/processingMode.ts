import type { ProcessingMode } from '../../api/types';
import { LLM_DISABLED_REASON } from '../settings/LlmGate';

// Hint shown below a processing-mode select; warning marks modes that can cut real audio.
export const PROCESSING_MODE_HINTS: Record<ProcessingMode, { text: string; warning: boolean }> = {
  standard: {
    text: 'Detects ads with the model and cuts them out. The default for most feeds.',
    warning: false,
  },
  keep_content: {
    text: 'Removes everything the model does not mark as show content. For feeds with '
      + 'unrecognizable inserted ads. Safety checks revert to normal removal when the '
      + 'labeling looks off, but they can miss a single mislabeled stretch and cut real '
      + 'audio. Check each episode.',
    warning: true,
  },
  skip_detection: {
    text: 'Episodes are still transcribed and get chapters and a transcript, but nothing '
      + 'is scanned for ads and nothing is cut. For ad-free shows; skips the ad '
      + 'detection cost.',
    warning: false,
  },
  passthrough: {
    text: 'Episodes are downloaded and served exactly as published: no transcription, ad '
      + 'detection, or cutting. The feed URL stays the same, so switching to another '
      + 'mode resumes processing for new episodes. Episodes already served untouched '
      + 'keep their original audio until you reprocess them.',
    warning: true,
  },
  cue_only: {
    text: 'Cuts come only from this feed\'s ad-break start and end cue templates: no '
      + 'model call, no verification pass, and no LLM redetection. Needs one enabled '
      + 'ad-break start template and one enabled ad-break end template.',
    warning: true,
  },
};

export const PROCESSING_MODE_LABELS: Record<ProcessingMode, string> = {
  standard: 'Standard (detect and cut ads)',
  keep_content: 'Keep content only (experimental)',
  skip_detection: 'Skip ad detection (transcripts and chapters only)',
  passthrough: 'Pass-through (serve upstream audio untouched)',
  cue_only: 'Cue-only (cut from audio cue templates, no LLM) (experimental)',
};

export const PROCESSING_MODE_ORDER: ProcessingMode[] = ['standard', 'keep_content', 'skip_detection', 'passthrough', 'cue_only'];

// cue_only needs cue templates, which a feed only has after creation.
export const CREATE_PROCESSING_MODES = PROCESSING_MODE_ORDER.filter((m) => m !== 'cue_only');

const KEEP_CONTENT_NONE_NOTE = 'Keep content runs as Standard while the LLM provider is set to None.';
const STANDARD_NONE_TEXT = 'Detects ads with text patterns, audio fingerprints, audio cues and cross-fetch comparison, and cuts them out. The default for most feeds.';
export const KEEP_CONTENT_DISABLED_NOTE = `Keep content only is unavailable. ${LLM_DISABLED_REASON}`;

// Hint for the selected mode; under None, keep_content and standard describe model-free detection.
export function processingModeHint(mode: ProcessingMode, llmEnabled: boolean): { text: string; warning: boolean } {
  if (llmEnabled) return PROCESSING_MODE_HINTS[mode];
  if (mode === 'keep_content') return { text: KEEP_CONTENT_NONE_NOTE, warning: false };
  if (mode === 'standard') return { text: STANDARD_NONE_TEXT, warning: false };
  return PROCESSING_MODE_HINTS[mode];
}
