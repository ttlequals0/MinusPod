import type { PatternAudioSummary } from '../api/patterns';
import { formatPercent } from './format';

// What the Audio column and card show for a pattern's audio summary.
export function audioSummaryLabel(audio?: PatternAudioSummary): string {
  if (!audio || audio.state === 'none') return '-';
  if (audio.state === 'off') return 'Audio off';
  if (!audio.available) return 'No source audio';
  if (audio.matchCount > 0 && audio.meanSimilarity != null) return `${formatPercent(audio.meanSimilarity)} match`;
  return '-';
}

const REASON_PATTERN = /pattern #(\d+)/i;

// Text-pattern markers name their pattern in the reason when the marker carries no id.
export function markerPatternId(m: { pattern_id?: number | null; reason?: string }): number | null {
  if (m.pattern_id != null) return m.pattern_id;
  const found = m.reason ? REASON_PATTERN.exec(m.reason) : null;
  return found ? Number(found[1]) : null;
}
