import { Link } from 'react-router-dom';
import { Episode } from '../api/types';
import { EPISODE_STATUS_COLORS, EPISODE_STATUS_LABELS } from '../utils/episodeStatus';
import { stripHtml } from '../utils/stripHtml';
import Checkbox from './Checkbox';

interface EpisodeListProps {
  episodes: Episode[];
  feedSlug: string;
  selectedIds?: Set<string>;
  onToggle?: (id: string) => void;
  onSelectAll?: (checked: boolean) => void;
}

function EpisodeList({ episodes, feedSlug, selectedIds, onToggle, onSelectAll }: EpisodeListProps) {
  if (episodes.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        No episodes found
      </div>
    );
  }

  const selectableEpisodes = episodes.filter(ep => ep.status !== 'processing');
  const allSelected = selectedIds && selectableEpisodes.length > 0 &&
    selectableEpisodes.every(ep => selectedIds.has(ep.id));

  return (
    <div className="space-y-2">
      {onSelectAll && selectedIds && (
        <div className="flex items-center gap-2 pl-3 py-2">
          <Checkbox
            checked={!!allSelected}
            onChange={(checked) => onSelectAll(checked)}
          />
          <span className="text-sm text-muted-foreground">Select all on page</span>
        </div>
      )}
      {episodes.map((episode) => (
        <EpisodeRow
          key={episode.id}
          episode={episode}
          feedSlug={feedSlug}
          selected={selectedIds?.has(episode.id) ?? false}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
}

function EpisodeRow({
  episode,
  feedSlug,
  selected,
  onToggle,
}: {
  episode: Episode;
  feedSlug: string;
  selected: boolean;
  onToggle?: (id: string) => void;
}) {
  const formatDuration = (seconds?: number) => {
    if (!seconds) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    return `${minutes}m`;
  };

  const canSelect = episode.status !== 'processing';

  return (
    <div className="relative bg-card rounded-lg border border-border hover:border-primary/50 transition-colors">
      {onToggle && canSelect && (
        <div
          className="absolute top-3 left-3 z-10"
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox
            checked={selected}
            onChange={() => onToggle(episode.id)}
          />
        </div>
      )}
      <Link
        to={`/feeds/${feedSlug}/episodes/${episode.id}`}
        className={`block p-4 ${onToggle ? 'pl-10' : ''}`}
      >
        <h3 className="font-medium text-foreground truncate">{episode.title}</h3>
        {episode.description && (
          <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
            {stripHtml(episode.description)}
          </p>
        )}
        <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
          <span>{new Date(episode.published).toLocaleDateString()}</span>
          {episode.duration && <span>{formatDuration(episode.duration)}</span>}
          {episode.ad_count !== undefined && episode.ad_count > 0 && (
            <span>{episode.ad_count} ads detected</span>
          )}
          <span className={`px-2 py-0.5 text-xs rounded-full whitespace-nowrap ${EPISODE_STATUS_COLORS[episode.status] || 'bg-muted text-muted-foreground'}`}>
            {EPISODE_STATUS_LABELS[episode.status] || episode.status}
          </span>
        </div>
      </Link>
    </div>
  );
}

export default EpisodeList;
