import type { AdSegment } from '../api/types';

type HasConfidence = Pick<AdSegment, 'confidence' | 'validation'>;

export function formatConfidence(seg: HasConfidence): string {
  const raw = Math.round(seg.confidence * 100);
  const adj = seg.validation?.adjusted_confidence;
  if (adj !== undefined && Math.abs(adj - seg.confidence) > 0.005) {
    return `${raw}% raw / ${Math.round(adj * 100)}% adjusted`;
  }
  return `${raw}% confidence`;
}
