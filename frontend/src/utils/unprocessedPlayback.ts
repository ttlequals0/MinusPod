import type { UnprocessedPlayback } from '../api/types';

export const UNPROCESSED_PLAYBACK_LABELS: Record<UnprocessedPlayback, string> = {
  process: 'Start processing',
  original: 'Play the original',
};

// What each option does, in plain words. The help line under the control shows
// the text for the option that is currently in effect.
export const UNPROCESSED_PLAYBACK_HELP: Record<UnprocessedPlayback, string> = {
  process:
    'A podcast app that plays an unprocessed episode waits while it is processed (it may show an error and retry).',
  original:
    'A podcast app that plays an unprocessed episode plays the original audio with ads until the ad-free version is ready; it then replaces it automatically.',
};
