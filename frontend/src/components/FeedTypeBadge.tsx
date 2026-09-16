import type { Feed } from '../api/types';
import { badgeBase, tint } from './badgeStyles';

const LABELS: Record<string, string> = { local: 'Local', recents: 'Recents' };

// Feed type chip for feeds without an upstream; subscribed feeds show nothing.
function FeedTypeBadge({ feedType, className = '' }: { feedType: Feed['feedType']; className?: string }) {
  const label = feedType ? LABELS[feedType] : undefined;
  if (!label) return null;
  return (
    <span className={`${badgeBase} shrink-0 font-medium ${tint.blue} ${className}`}>
      {label}
    </span>
  );
}

export default FeedTypeBadge;
