import { ReactNode } from 'react';
import { Link } from 'react-router';
import { Episode } from '../api/types';
import { displayStatusColor, displayStatusLabel, isFailedStatus } from '../utils/episodeStatus';
import { stripHtml } from '../utils/stripHtml';
import { formatDate } from '../utils/format';
import Artwork from './Artwork';
import { episodeArtworkSrc } from '../utils/artworkUrl';
import Checkbox from './Checkbox';
import { focusRing } from './fieldStyles';
import { isActionBlocked } from '../utils/processingStage';
import { badgeBase, tint } from './badgeStyles';

interface EpisodeListProps {
  episodes: Episode[];
  feedSlug: string;
  feedArtworkUrl?: string;
  selectedIds?: Set<string>;
  onToggle?: (id: string, shiftKey: boolean) => void;
  onSelectAll?: (checked: boolean) => void;
}

function EpisodeList({ episodes, feedSlug, feedArtworkUrl, selectedIds, onToggle, onSelectAll }: EpisodeListProps) {
  if (episodes.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        No episodes found
      </div>
    );
  }

  const selectableEpisodes = episodes.filter(ep => !isActionBlocked(ep.jobState, false));
  const allSelected = selectedIds && selectableEpisodes.length > 0 &&
    selectableEpisodes.every(ep => selectedIds.has(ep.id));

  return (
    <div className="space-y-2">
      {onSelectAll && selectedIds && (
        <div className="flex items-center gap-2 pl-3 py-2">
          <Checkbox
            ariaLabel="Select all on page"
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
          feedArtworkUrl={feedArtworkUrl}
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
  feedArtworkUrl,
  selected,
  onToggle,
  renderActions,
}: {
  episode: Episode;
  feedSlug: string;
  feedArtworkUrl?: string;
  selected: boolean;
  onToggle?: (id: string, shiftKey: boolean) => void;
  // Optional per-row control (e.g. a process/reprocess menu) rendered outside
  // the episode Link so it never nests an interactive element inside an <a>.
  renderActions?: (episode: Episode) => ReactNode;
}) {
  // Rows of the recents feed belong to another feed; link there.
  const rowSlug = episode.feedSlug ?? feedSlug;
  const formatDuration = (seconds?: number) => {
    if (!seconds) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    return `${minutes}m`;
  };

  const canSelect = !isActionBlocked(episode.jobState, false);
  const failureReason =
    isFailedStatus(episode.status) && episode.error ? episode.error : undefined;

  return (
    <div className="relative flex items-stretch bg-card rounded-lg border border-border hover:border-primary/50 transition-colors">
      {onToggle && canSelect && (
        // 44x44 tap zone (iOS HIG minimum); visible checkbox centered inside.
        // onClick + onTouchEnd both stopPropagation so the underlying Link
        // doesn't fire when a finger lands slightly off the 16px control.
        <button
          type="button"
          aria-label={selected ? 'Deselect episode' : 'Select episode'}
          onClick={(e) => { e.stopPropagation(); e.preventDefault(); onToggle(episode.id, e.shiftKey); }}
          onTouchEnd={(e) => { e.stopPropagation(); }}
          className={`absolute top-0 left-0 z-10 h-11 w-11 flex items-center justify-center ${focusRing}`}
        >
          <Checkbox id={`select-${episode.id}`} checked={selected} onChange={() => {}} />
        </button>
      )}
      <Link
        to={`/feeds/${rowSlug}/episodes/${episode.id}`}
        className={`flex-1 min-w-0 flex gap-3 p-4 ${onToggle ? 'pl-12' : ''} ${focusRing}`}
      >
        <Artwork
          // A recents row falls back to its source feed's cover, not this feed's.
          src={episodeArtworkSrc(rowSlug, episode.id, episode.artworkUrl, episode.feedSlug ? undefined : feedArtworkUrl)}
          alt=""
          loading="lazy"
          className="w-12 h-12 sm:w-16 sm:h-16 shrink-0 object-cover rounded-md"
        />
        <div className="flex-1 min-w-0">
          <h3 className="font-medium text-foreground truncate">{episode.title}</h3>
          {episode.feedTitle && (
            <p className="text-xs text-muted-foreground truncate">{episode.feedTitle}</p>
          )}
          {/* Fixed slots (two description lines, one meta line, one badge
              line) so every row in the list is the same height. */}
          <p className="text-sm text-muted-foreground mt-1 line-clamp-2 min-h-10">
            {episode.description ? stripHtml(episode.description, { collapse: true }) : ''}
          </p>
          <div className="flex gap-x-3 mt-2 text-sm text-muted-foreground truncate">
            <span className="whitespace-nowrap">{formatDate(episode.published)}</span>
            {episode.duration && <span className="whitespace-nowrap">{formatDuration(episode.duration)}</span>}
            {episode.ad_count !== undefined && episode.ad_count > 0 && (
              <span className="whitespace-nowrap">{episode.ad_count} ads detected</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1 min-h-6">
            {episode.pendingReviewCount !== undefined && episode.pendingReviewCount > 0 && (
              <span className={`${badgeBase} whitespace-nowrap ${tint.warning}`}>
                {episode.pendingReviewCount} held
              </span>
            )}
            {episode.passthroughEnabled && (
              <span
                className={`${badgeBase} whitespace-nowrap ${tint.neutral}`}
                title="Served unmodified; ad processing is skipped for this episode"
              >
                Pass-through
              </span>
            )}
            <span
              className={`${badgeBase} whitespace-nowrap ${displayStatusColor(episode.status, episode.jobState)}${failureReason ? ' cursor-help' : ''}`}
              title={failureReason}
            >
              {displayStatusLabel(episode.status, episode.jobState)}
            </span>
          </div>
        </div>
      </Link>
      {renderActions && (
        <div className="flex items-center pr-4 shrink-0">
          {renderActions(episode)}
        </div>
      )}
    </div>
  );
}

export { EpisodeRow };
export default EpisodeList;
