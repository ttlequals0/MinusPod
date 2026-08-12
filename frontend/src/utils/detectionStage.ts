export type DetectionStage =
  | 'first_pass'
  | 'claude'
  | 'fingerprint'
  | 'text_pattern'
  | 'language'
  | 'verification'
  | 'manual'
  | 'cue_pair'
  | 'keep_content'
  | 'vad_gap'
  | 'heuristic_preroll'
  | 'heuristic_postroll'
  | 'dai_differential';

export const DETECTION_STAGE_META: Record<DetectionStage, { label: string; className: string }> = {
  first_pass: {
    label: 'Pass 1',
    className: 'bg-c-blue/20 text-c-blue',
  },
  claude: {
    label: 'Pass 1',
    className: 'bg-c-blue/20 text-c-blue',
  },
  fingerprint: {
    label: 'Pass 1',
    className: 'bg-c-blue/20 text-c-blue',
  },
  text_pattern: {
    label: 'Pass 1',
    className: 'bg-c-blue/20 text-c-blue',
  },
  language: {
    label: 'Pass 1',
    className: 'bg-c-blue/20 text-c-blue',
  },
  verification: {
    label: 'Pass 2',
    className: 'bg-c-purple/20 text-c-purple',
  },
  manual: {
    label: 'Manual',
    className: 'bg-warning/20 text-warning',
  },
  cue_pair: {
    label: 'Cue pair',
    className: 'bg-c-purple/20 text-c-purple',
  },
  keep_content: {
    label: 'Keep-content',
    className: 'bg-c-teal/20 text-c-teal',
  },
  vad_gap: {
    label: 'VAD gap',
    className: 'bg-c-teal/20 text-c-teal',
  },
  heuristic_preroll: {
    label: 'Pre-roll',
    className: 'bg-success/20 text-success',
  },
  heuristic_postroll: {
    label: 'Post-roll',
    className: 'bg-success/20 text-success',
  },
  dai_differential: {
    label: 'Cross-fetch',
    className: 'bg-destructive/20 text-destructive',
  },
};
