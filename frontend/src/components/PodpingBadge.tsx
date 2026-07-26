import { Feed } from '../api/types';
import { formatDate, formatDateTime } from '../utils/format';

interface PodpingBadgeProps {
  coverage: Feed['podpingCoverage'];
  lastPodpingAt?: string | null;
  compact?: boolean;
  className?: string;
}

function PodpingBadge({ coverage, lastPodpingAt, compact, className }: PodpingBadgeProps) {
  // Null coverage means the listener is off instance-wide, which is not a fact
  // about this feed, so say nothing.
  if (!coverage) return null;

  const received = coverage === 'received' && lastPodpingAt;
  const label = received
    ? (compact
        ? `Podping: ${formatDate(lastPodpingAt)}`
        : `Podping: last ping at ${formatDateTime(lastPodpingAt)}`)
    : 'Podping: never';

  const title = received
    ? 'This feed is refreshed by Podping instead of waiting for the next scheduled poll.'
    : 'No Podping has ever arrived for this feed, so it updates on the refresh schedule.';

  const tone = received ? 'text-foreground' : 'text-muted-foreground';
  return <span title={title} className={`${tone} ${className ?? ''}`.trim()}>{label}</span>;
}

export default PodpingBadge;
