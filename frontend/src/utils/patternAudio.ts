import type { PatternAudioSummary } from '../api/patterns';
import { formatPercent } from './format';

// What the Audio column and card show for a pattern's audio summary.
export function audioSummaryLabel(audio?: PatternAudioSummary): string {
  if (!audio || audio.state === 'none') return 'No audio';
  if (audio.state === 'off') return 'Audio off';
  if (!audio.available) return 'No source audio';
  if (audio.matchCount > 0 && audio.meanSimilarity != null) return `${formatPercent(audio.meanSimilarity)} match`;
  return 'No matches yet';
}
