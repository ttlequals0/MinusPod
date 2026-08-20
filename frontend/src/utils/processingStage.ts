// Human-readable labels for the pipeline stage StatusService reports on the
// active job. Shared by the global status bar and the settings Processing
// Queue panel.

const STAGE_LABELS: Record<string, string> = {
  downloading: 'Downloading',
  transcribing: 'Transcribing',
  detecting: 'Detecting ads',
  analyzing: 'Analyzing audio',
  processing: 'Processing audio',
  verifying: 'Verifying',
  complete: 'Complete',
  queued: 'Queued',
  // Pass-prefixed stages
  'pass1:transcribing': 'Pass 1: Transcribing',
  'pass1:analyzing': 'Pass 1: Analyzing audio',
  'pass1:detecting': 'Pass 1: Detecting ads',
  'pass1:reviewing': 'Pass 1: Reviewing detections',
  'pass1:processing': 'Pass 1: Processing audio',
  'pass2:transcribing': 'Pass 2: Transcribing',
  'pass2:analyzing': 'Pass 2: Analyzing audio',
  'pass2:detecting': 'Pass 2: Detecting ads',
  'pass2:reviewing': 'Pass 2: Reviewing detections',
};

export function getStageLabel(stage: string): string {
  // Direct match
  if (STAGE_LABELS[stage]) {
    return STAGE_LABELS[stage];
  }
  // Handle substages like "pass1:detecting:2/5" or "detecting:2/5"
  const parts = stage.split(':');
  if (parts.length >= 2) {
    // Try pass-prefixed: "pass1:detecting"
    const passKey = `${parts[0]}:${parts[1]}`;
    if (STAGE_LABELS[passKey]) {
      const windowInfo = parts.length >= 3 ? ` (${parts[2]})` : '';
      return STAGE_LABELS[passKey] + windowInfo;
    }
    // Try base stage: "detecting"
    if (STAGE_LABELS[parts[0]]) {
      const windowInfo = parts.length >= 2 ? ` (${parts[1]})` : '';
      return STAGE_LABELS[parts[0]] + windowInfo;
    }
  }
  return stage;
}
