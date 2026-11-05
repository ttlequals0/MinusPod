import { PROCESSED_TITLE_MARKER_MAX_LENGTH } from '../api/types';

// Same rule as the server's validate_processed_title_marker, worded for people.
// Control, invisible (format) and line/paragraph separator characters are
// never allowed.
const HIDDEN_CHARS = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;

export function markerProblem(marker: string): string | null {
  if (marker === '') return null;
  if (marker.trim() === '') return "The marker can't be only spaces.";
  if (marker.length > PROCESSED_TITLE_MARKER_MAX_LENGTH) {
    return `The marker can be at most ${PROCESSED_TITLE_MARKER_MAX_LENGTH} characters.`;
  }
  if (HIDDEN_CHARS.test(marker)) return "The marker can't contain hidden or control characters.";
  return null;
}
