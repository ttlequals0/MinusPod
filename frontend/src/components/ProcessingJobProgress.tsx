import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { getStageLabel } from '../utils/processingStage';
import { focusRing } from './fieldStyles';

export interface ProcessingJobProgressData {
  slug: string;
  episodeId: string;
  title: string;
  podcastName: string;
  stage: string;
  progress: number;
}

function clampProgress(progress: number): number {
  return Math.max(0, Math.min(100, Number.isFinite(progress) ? progress : 0));
}

export function formatJobDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0s';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}

export default function ProcessingJobProgress({
  job,
  elapsed,
  linkTo,
  actions,
  className = '',
  testId,
  compact = false,
}: {
  job: ProcessingJobProgressData;
  elapsed: number;
  linkTo?: string;
  actions?: ReactNode;
  className?: string;
  testId?: string;
  compact?: boolean;
}) {
  const stageLabel = getStageLabel(job.stage);
  const progress = clampProgress(job.progress);
  const title = linkTo ? (
    <Link to={linkTo} className={`block font-medium text-primary hover:underline truncate ${compact ? 'text-sm' : ''} ${focusRing}`}>
      {job.title}
    </Link>
  ) : (
    <p className={`${compact ? 'text-sm' : ''} font-medium text-foreground truncate`}>{job.title}</p>
  );

  return (
    <div className={className} data-testid={testId}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {title}
          <p className={`${compact ? 'text-xs' : 'text-sm'} text-muted-foreground truncate`}>{job.podcastName}</p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-sm font-medium text-primary">{stageLabel}</p>
          <p className="text-xs text-muted-foreground tabular-nums">
            {Math.round(progress)}% - {formatJobDuration(elapsed)}
          </p>
        </div>
      </div>
      <div
        className={`${compact ? 'mt-2' : 'mt-3'} h-2 bg-muted rounded-full overflow-hidden`}
        role="progressbar"
        aria-label={`${stageLabel} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <div className="h-full bg-primary transition-all duration-300" style={{ width: `${progress}%` }} />
      </div>
      {actions && <div className="mt-3 flex justify-end">{actions}</div>}
    </div>
  );
}
