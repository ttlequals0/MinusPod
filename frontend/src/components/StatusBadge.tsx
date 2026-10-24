import { badgeBase } from './badgeStyles';
import { displayStatusColor, displayStatusLabel } from '../utils/episodeStatus';

// Short form drops qualifiers: "permanently failed" -> "failed", "queued (offline)" -> "queued".
function shortLabel(label: string): string {
  return label.replace(/^permanently /, '').replace(/ \(.*\)$/, '');
}

function sentenceCase(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export default function StatusBadge({ status, jobState, short, className, title }: {
  status: string;
  jobState?: string;
  short?: boolean;
  className?: string;
  title?: string;
}) {
  const label = displayStatusLabel(status, jobState);
  return (
    <span className={`${badgeBase} ${displayStatusColor(status, jobState)}${className ? ` ${className}` : ''}`} title={title}>
      {sentenceCase(short ? shortLabel(label) : label)}
    </span>
  );
}
