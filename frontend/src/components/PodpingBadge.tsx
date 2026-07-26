import { Feed } from '../api/types';
import { formatDate, formatDateTime } from '../utils/format';

interface PodpingBadgeProps {
  coverage: Feed['podpingCoverage'];
  lastPodpingAt?: string | null;
  compact?: boolean;
  className?: string;
}

const TITLES = {
  received: 'This feed is refreshed by Podping instead of waiting for the next scheduled poll.',
  declared: 'This feed declares that it uses Podping, but no notification has arrived for it yet.',
  never: 'No Podping has ever arrived for this feed, so it updates on the refresh schedule.',
};

function PodpingBadge({ coverage, lastPodpingAt, compact, className }: PodpingBadgeProps) {
  // Null coverage means the listener is off instance-wide, which is not a fact
  // about this feed, so say nothing.
  if (!coverage) return null;

  // Everything except a received ping and a publisher opt-in reads as never:
  // the finer API states are diagnostics, not something to make the reader parse.
  let state: keyof typeof TITLES = 'never';
  if (coverage === 'received' && lastPodpingAt) state = 'received';
  else if (coverage === 'declared') state = 'declared';

  let label: string;
  if (state === 'received') {
    label = compact
      ? `Podping: ${formatDate(lastPodpingAt ?? null)}`
      : `Podping: last ping at ${formatDateTime(lastPodpingAt ?? null)}`;
  } else if (state === 'declared') {
    label = compact ? 'Podping: enabled' : 'Podping: enabled, none received yet';
  } else {
    label = 'Podping: never';
  }

  const tone = state === 'never' ? 'text-muted-foreground' : 'text-foreground';
  return (
    <span title={TITLES[state]} className={`${tone} ${className ?? ''}`.trim()}>
      {label}
    </span>
  );
}

export default PodpingBadge;
