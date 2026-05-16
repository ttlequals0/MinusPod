export type DetectionStage =
  | 'first_pass'
  | 'claude'
  | 'fingerprint'
  | 'text_pattern'
  | 'language'
  | 'verification'
  | 'manual';

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
};
