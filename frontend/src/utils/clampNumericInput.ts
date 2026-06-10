/**
 * Resolve a numeric settings input on edit: an empty field returns the
 * fallback, an unparseable value returns undefined (caller should skip the
 * update), and any other value is clamped to [lo, hi]. Shared by the settings
 * sections so an out-of-range value never reaches Save (the backend rejects it
 * anyway).
 */
export function clampNumericInput(
  raw: string,
  lo: number,
  hi: number,
  fallback: number,
  parse: (s: string) => number,
): number | undefined {
  if (raw === '') return fallback;
  const v = parse(raw);
  if (!Number.isFinite(v)) return undefined;
  return Math.max(lo, Math.min(hi, v));
}
