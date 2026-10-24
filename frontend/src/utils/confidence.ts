import type { AdSegment } from '../api/types';
import { formatPercent } from './format';

type HasConfidence = Pick<AdSegment, 'confidence' | 'validation'>;

export function formatConfidence(seg: HasConfidence): string {
  const adj = seg.validation?.adjusted_confidence;
  if (adj !== undefined && Math.abs(adj - seg.confidence) > 0.005) {
    return `${formatPercent(seg.confidence)} raw / ${formatPercent(adj)} adjusted`;
  }
  return `${formatPercent(seg.confidence)} confidence`;
}
