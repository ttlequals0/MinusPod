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
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  },
  claude: {
    label: 'Pass 1',
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  },
  fingerprint: {
    label: 'Pass 1',
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  },
  text_pattern: {
    label: 'Pass 1',
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  },
  language: {
    label: 'Pass 1',
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  },
  verification: {
    label: 'Pass 2',
    className: 'bg-purple-500/20 text-purple-600 dark:text-purple-400',
  },
  manual: {
    label: 'Manual',
    className: 'bg-amber-500/20 text-amber-600 dark:text-amber-400',
  },
  cue_pair: {
    label: 'Cue pair',
    className: 'bg-violet-500/20 text-violet-600 dark:text-violet-400',
  },
  keep_content: {
    label: 'Keep-content',
    className: 'bg-teal-500/20 text-teal-600 dark:text-teal-400',
  },
  vad_gap: {
    label: 'VAD gap',
    className: 'bg-cyan-500/20 text-cyan-600 dark:text-cyan-400',
  },
  heuristic_preroll: {
    label: 'Pre-roll',
    className: 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  },
  heuristic_postroll: {
    label: 'Post-roll',
    className: 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  },
  dai_differential: {
    label: 'Cross-fetch',
    className: 'bg-rose-500/20 text-rose-600 dark:text-rose-400',
  },
};
