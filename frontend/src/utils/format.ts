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
