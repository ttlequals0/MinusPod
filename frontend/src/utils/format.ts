// Shared, output-stable formatting helpers. Each function here had multiple
// byte-identical copies inlined across pages/components before consolidation.

// Clock-style timestamp: `H:MM:SS` at or above one hour, else `M:SS`. Integer
// seconds (each field floored). Used for audio positions (cue rows, ad
// boundaries) and, via settingsUtils.formatDuration, episode-length durations.
// NaN, Infinity and negatives read 0:00.
export function formatTimestamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
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

// Display name for a subscriber key, which may have no label.
export function keyLabel(key: { label: string }): string {
  return key.label || 'Unlabeled subscriber';
}

// Locale date plus short time (e.g. "7/12/2026, 3:26 PM"), `-` for a
// missing value. Used where the freshness of a timestamp matters (feed
// refresh times).
export function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '-';
  return new Date(dateStr).toLocaleString([], {
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

// Past time relative to now ("just now", "5m ago", "3h ago", "2d ago").
export function formatTimeAgo(dateStr: string, now = Date.now()): string {
  const minutes = Math.floor((now - new Date(dateStr).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// Compact stats duration: `Ns` under a minute, `N.Nm` under an hour, else
// `N.Nh`. Distinct from formatTimestamp (clock-style) and
// settingsUtils.formatDuration (episode lengths).
export function formatStatsDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

// Episode-list duration: `Xh Ym` at or above one hour, else `Xm`. '' for a
// falsy value (0, null, undefined).
export function formatDurationHoursMinutes(seconds?: number): string {
  if (!seconds) return '';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

// First letter upper-cased.
export function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// LLM cost with sub-cent precision.
export function formatCost(cost: number): string {
  return `$${cost.toFixed(4)}`;
}

// ISO datetime -> the value a <input type="datetime-local"> expects
// (local time, 'YYYY-MM-DDTHH:mm'). '' for a missing/unparseable value.
export function toDatetimeLocalInput(dateStr?: string | null): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Reverse of toDatetimeLocalInput: a datetime-local input value (read by the
// Date constructor in the browser's local timezone) to the ISO 8601 UTC
// string the backend accepts. '' -> undefined (field left blank).
export function fromDatetimeLocalInput(value: string): string | undefined {
  if (!value) return undefined;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

// Whole-number percent. Values <= 1 are fractions (0.99 -> '99%'); larger values are already percent.
export function formatPercent(value: number): string {
  const pct = value <= 1 ? value * 100 : value;
  return `${Math.round(pct)}%`;
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

// Reads over input; null when there is nothing to show (no input, or no cache traffic).
function cacheHitRate(read: number, write: number, input: number): number | null {
  if (!input || (!read && !write)) return null;
  return Math.min(read / input, 1);
}

// text is '' when there is no rate to show.
export function cacheHit(read: number, write: number, input: number): { text: string; title?: string } {
  const rate = cacheHitRate(read, write, input);
  if (rate == null) return { text: '' };
  return {
    text: formatPercent(rate),
    title: `${formatTokenCount(read)} read, ${formatTokenCount(write)} write of ${formatTokenCount(input)} input`,
  };
}

// Elapsed or summed duration, rounded to whole seconds; zero trailing units are dropped: '45s', '2m 5s', '1h 2m'.
export function formatElapsed(seconds: number): string {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  return `${s}s`;
}
