import { badgeBase } from './badgeStyles';
import { displayStatusColor, displayStatusLabel } from '../utils/episodeStatus';
import { sentenceCase } from '../utils/format';

export default function StatusBadge({ status, jobState, className, title }: {
  status: string;
  jobState?: string;
  className?: string;
  title?: string;
}) {
  return (
    <span className={`${badgeBase} ${displayStatusColor(status, jobState)} ${className ?? ''}`} title={title}>
      {sentenceCase(displayStatusLabel(status, jobState))}
    </span>
  );
}
