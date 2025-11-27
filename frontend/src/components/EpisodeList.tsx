import { Link } from 'react-router-dom';
import { Episode } from '../api/types';

interface EpisodeListProps {
  episodes: Episode[];
  feedSlug: string;
}

function EpisodeList({ episodes, feedSlug }: EpisodeListProps) {
  if (episodes.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        No episodes found
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {episodes.map((episode) => (
        <EpisodeRow key={episode.id} episode={episode} feedSlug={feedSlug} />
      ))}
    </div>
  );
}

function EpisodeRow({ episode, feedSlug }: { episode: Episode; feedSlug: string }) {
  const statusColors = {
    pending: 'bg-muted text-muted-foreground',
    processing: 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400',
    completed: 'bg-green-500/20 text-green-600 dark:text-green-400',
    failed: 'bg-destructive/20 text-destructive',
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    return `${minutes}m`;
  };

  return (
    <Link
      to={`/feeds/${feedSlug}/episodes/${episode.id}`}
      className="block bg-card rounded-lg border border-border p-4 hover:border-primary/50 transition-colors"
    >
      <div className="flex justify-between items-start gap-4">
        <div className="min-w-0 flex-1">
          <h3 className="font-medium text-foreground truncate">{episode.title}</h3>
          <div className="flex items-center gap-3 mt-1 text-sm text-muted-foreground">
            <span>{new Date(episode.published).toLocaleDateString()}</span>
            {episode.duration && <span>{formatDuration(episode.duration)}</span>}
            {episode.ad_count !== undefined && episode.ad_count > 0 && (
              <span>{episode.ad_count} ads detected</span>
            )}
          </div>
        </div>
        <span className={`px-2 py-1 text-xs rounded-full ${statusColors[episode.status]}`}>
          {episode.status}
        </span>
      </div>
    </Link>
  );
}

export default EpisodeList;
