// Shared, output-stable formatting helpers. Each function here had multiple
// byte-identical copies inlined across pages/components before consolidation.

// Clock-style timestamp: `H:MM:SS` at or above one hour, else `M:SS`. Integer
// seconds (each field floored). Used for audio positions (cue rows, ad
// boundaries) and, via settingsUtils.formatDuration, episode-length durations.
export function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// Locale date (no time), `-` for a missing value.
export function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-';
  return new Date(dateStr).toLocaleDateString();
}
