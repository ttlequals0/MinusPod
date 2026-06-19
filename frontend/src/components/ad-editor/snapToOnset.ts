// Snap a time to the nearest audible onset using the waveform peak buckets
// (#350 cue capture). A stinger starts with a sharp amplitude rise; we find
// the steepest positive step within a small radius of the requested time and
// snap to the bucket where that rise begins. Returns the input unchanged when
// no clear onset is nearby (e.g. a ramped swoosh), so snapping is an assist,
// never mandatory.

const SEARCH_RADIUS_SECONDS = 0.3;
// The rise must clear this fraction of the local amplitude range to count as a
// real onset rather than waveform noise.
const MIN_RISE_FRACTION = 0.25;

export function snapToOnset(
  timeSec: number,
  peaks: number[] | null,
  peakResolutionMs: number,
): number {
  if (!peaks || peaks.length < 3 || peakResolutionMs <= 0) return timeSec;
  const bucket = peakResolutionMs / 1000;
  const center = Math.round(timeSec / bucket);
  const radius = Math.max(1, Math.round(SEARCH_RADIUS_SECONDS / bucket));
  const lo = Math.max(1, center - radius);
  const hi = Math.min(peaks.length - 1, center + radius);
  if (hi <= lo) return timeSec;

  // Local amplitude range, for a relative rise threshold.
  let localMin = Infinity;
  let localMax = -Infinity;
  for (let i = lo - 1; i <= hi; i++) {
    const v = Math.abs(peaks[i]);
    if (v < localMin) localMin = v;
    if (v > localMax) localMax = v;
  }
  const range = localMax - localMin;
  if (range <= 0) return timeSec;

  let bestIdx = -1;
  let bestRise = 0;
  for (let i = lo; i <= hi; i++) {
    const rise = Math.abs(peaks[i]) - Math.abs(peaks[i - 1]);
    if (rise > bestRise) {
      bestRise = rise;
      bestIdx = i;
    }
  }
  if (bestIdx < 0 || bestRise < range * MIN_RISE_FRACTION) return timeSec;
  return bestIdx * bucket;
}
