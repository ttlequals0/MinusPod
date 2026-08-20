import type { LowAdYieldAction } from '../api/types';

// Option order is the escalation order: do nothing, redetect from the stored
// transcript, reprocess from source, then a full analysis with no patterns.
export const LOW_AD_YIELD_ACTION_LABELS: Record<LowAdYieldAction, string> = {
  nothing: 'Do nothing',
  redetect: 'Redetect ads',
  reprocess: 'Reprocess',
  full: 'Full analysis',
};
