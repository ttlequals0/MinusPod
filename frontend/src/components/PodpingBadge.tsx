import { Feed } from '../api/types';
import { formatDateTime } from '../utils/format';

interface PodpingBadgeProps {
  coverage: Feed['podpingCoverage'];
  lastPodpingAt?: string | null;
  compact?: boolean;
  className?: string;
}

const TITLES = {
  received: 'This feed is updated by Podping instead of waiting for the next scheduled poll.',
  host_active: 'This feed host sends Podpings, but none has arrived for this feed yet.',
  unseen: 'This host has not been seen sending Podpings in the last 30 days, so this feed updates on the refresh schedule.',
  declined: 'This feed asks not to be notified by Podping, so MinusPod polls it on the refresh schedule.',
};

function PodpingBadge({ coverage, lastPodpingAt, compact, className }: PodpingBadgeProps) {
  if (!coverage) return null;

  let label: string;
  if (coverage === 'received') {
    label = compact
      ? 'Podping'
      : (lastPodpingAt ? `Last podping: ${formatDateTime(lastPodpingAt)}` : 'Podping: received');
  } else if (coverage === 'host_active') {
    label = compact ? 'Podping host' : 'Podping: host sends, none for this feed yet';
  } else if (coverage === 'declined') {
    label = compact ? 'Polling' : 'Podping: declined by this feed';
  } else {
    label = compact ? 'Polling' : 'Podping: not seen from this host';
  }

  // Compact mode drops the timestamp, so keep it reachable on hover.
  const title = coverage === 'received' && lastPodpingAt
    ? `Last podping: ${formatDateTime(lastPodpingAt)}. ${TITLES.received}`
    : TITLES[coverage];

  const tone = coverage === 'received' ? 'text-foreground' : 'text-muted-foreground';
  return <span title={title} className={`${tone} ${className ?? ''}`.trim()}>{label}</span>;
}

export default PodpingBadge;
