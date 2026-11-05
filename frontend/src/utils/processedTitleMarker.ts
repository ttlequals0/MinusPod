import { PROCESSED_TITLE_MARKER_MAX_LENGTH } from '../api/types';

// Same rule, order and unit as the server's validate_processed_title_marker,
// worded for people. Length counts characters (Unicode code points), never
// UTF-16 units, so 16 emoji fit. Control, invisible (format) and
// line/paragraph separator characters are never allowed.
const HIDDEN_CHARS = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;

export function markerProblem(marker: string): string | null {
  if (marker === '') return null;
  if ([...marker].length > PROCESSED_TITLE_MARKER_MAX_LENGTH) {
    return `The marker can be at most ${PROCESSED_TITLE_MARKER_MAX_LENGTH} characters.`;
  }
  const hidden = marker.match(HIDDEN_CHARS);
  if (hidden) {
    const code = hidden[0].codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0');
    return `The marker can't contain a hidden or control character (U+${code}).`;
  }
  if (marker.trim() === '') return "The marker can't be only spaces.";
  return null;
}
