import type { Feed } from '../api/types';

const LABELS: Record<string, string> = { local: 'Local', recents: 'Recents' };

// Feed type chip for feeds without an upstream; subscribed feeds show nothing.
function FeedTypeBadge({ feedType, className = '' }: { feedType: Feed['feedType']; className?: string }) {
  const label = feedType ? LABELS[feedType] : undefined;
  if (!label) return null;
  return (
    <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-c-blue/15 text-c-blue ${className}`}>
      {label}
    </span>
  );
}

export default FeedTypeBadge;
