import { tint } from '../components/badgeStyles';

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
    className: tint.blue,
  },
  claude: {
    label: 'Pass 1',
    className: tint.blue,
  },
  fingerprint: {
    label: 'Pass 1',
    className: tint.blue,
  },
  text_pattern: {
    label: 'Pass 1',
    className: tint.blue,
  },
  language: {
    label: 'Pass 1',
    className: tint.blue,
  },
  verification: {
    label: 'Pass 2',
    className: tint.purple,
  },
  manual: {
    label: 'Manual',
    className: tint.warning,
  },
  cue_pair: {
    label: 'Cue pair',
    className: tint.purple,
  },
  keep_content: {
    label: 'Keep-content',
    className: tint.teal,
  },
  vad_gap: {
    label: 'VAD gap',
    className: tint.teal,
  },
  heuristic_preroll: {
    label: 'Pre-roll',
    className: tint.success,
  },
  heuristic_postroll: {
    label: 'Post-roll',
    className: tint.success,
  },
  dai_differential: {
    label: 'Cross-fetch',
    className: tint.destructive,
  },
};
