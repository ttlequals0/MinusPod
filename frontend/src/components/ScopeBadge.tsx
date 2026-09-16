import { AdPattern } from '../api/patterns';
import { badgeBase, tint } from './badgeStyles';

// Pattern scope badge shared by PatternsPage and PatternDetailModal. The
// podcast variant takes an optional className so list rows can truncate the
// podcast name.
export function ScopeBadge({
  pattern,
  podcastClassName,
}: {
  pattern: AdPattern;
  podcastClassName?: string;
}) {
  if (pattern.scope === 'global') {
    return <span className={`${badgeBase} ${tint.blue}`}>Global</span>;
  }
  if (pattern.scope === 'network') {
    return <span className={`${badgeBase} ${tint.purple}`}>Network: {pattern.network_id}</span>;
  }
  if (pattern.scope === 'podcast') {
    return (
      <span className={`${badgeBase} ${tint.success}${podcastClassName ? ` ${podcastClassName}` : ''}`}>
        {pattern.podcast_name || 'Podcast'}
      </span>
    );
  }
  return null;
}
