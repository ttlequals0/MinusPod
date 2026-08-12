import { AdPattern } from '../api/patterns';

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
    return <span className="px-2 py-0.5 text-xs rounded bg-c-blue/20 text-c-blue">Global</span>;
  }
  if (pattern.scope === 'network') {
    return <span className="px-2 py-0.5 text-xs rounded bg-c-purple/20 text-c-purple">Network: {pattern.network_id}</span>;
  }
  if (pattern.scope === 'podcast') {
    return (
      <span className={`px-2 py-0.5 text-xs rounded bg-success/20 text-success${podcastClassName ? ` ${podcastClassName}` : ''}`}>
        {pattern.podcast_name || 'Podcast'}
      </span>
    );
  }
  return null;
}
