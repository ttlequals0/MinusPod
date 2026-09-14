import { Link } from 'react-router';
import { Feed, Episode, EpisodeSummary } from '../api/types';
import { feedDisplayTitle } from '../utils/feedTitle';
import { feedArtworkSrc } from '../utils/artworkUrl';
import Artwork from './Artwork';
import FeedTypeBadge from './FeedTypeBadge';
import { EpisodeRow } from './EpisodeList';
import EpisodeRowActions from './EpisodeRowActions';
import { focusRing } from './fieldStyles';

export const MIN_EPISODES_PER_PODCAST = 1;
export const MAX_EPISODES_PER_PODCAST = 10;
export const DEFAULT_EPISODES_PER_PODCAST = 3;

export function clampEpisodesPerPodcast(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_EPISODES_PER_PODCAST;
  return Math.min(MAX_EPISODES_PER_PODCAST, Math.max(MIN_EPISODES_PER_PODCAST, Math.round(value)));
}

// EpisodeSummary trims fields EpisodeRow doesn't require (feedSlug, ad_count,
// pendingReviewCount, error); this fills in the rest as undefined.
function toEpisode(summary: EpisodeSummary): Episode {
  return {
    id: summary.id,
    title: summary.title,
    published: summary.published,
    duration: summary.duration,
    status: summary.status,
    jobState: summary.jobState,
    artworkUrl: summary.artworkUrl ?? undefined,
    description: summary.description ?? undefined,
  };
}

interface DashboardEpisodeGroupsProps {
  feeds: Feed[];
  episodesPerPodcast: number;
}

// One section per podcast with its latest episodes. Recents is excluded: its
// episodes belong to other podcasts' podcast_id, so latestEpisodes is always
// empty for it and it would otherwise render as a broken, empty card.
function DashboardEpisodeGroups({ feeds, episodesPerPodcast }: DashboardEpisodeGroupsProps) {
  const groupedFeeds = feeds.filter((feed) => feed.feedType !== 'recents');

  if (groupedFeeds.length === 0) {
    return (
      <div className="text-center py-12 bg-card rounded-lg border border-border">
        <p className="text-muted-foreground">No podcasts to show episodes for yet</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {groupedFeeds.map((feed) => (
        <FeedEpisodeGroup key={feed.slug} feed={feed} limit={episodesPerPodcast} />
      ))}
    </div>
  );
}

function FeedEpisodeGroup({ feed, limit }: { feed: Feed; limit: number }) {
  const episodes = (feed.latestEpisodes ?? []).slice(0, limit);
  const artworkUrl = feedArtworkSrc(feed.slug, feed.artworkUrl);
  const headingId = `episode-group-heading-${feed.slug}`;

  return (
    <section aria-labelledby={headingId} className="bg-card rounded-lg border border-border overflow-hidden">
      <div className="flex items-center gap-3 p-4 border-b border-border">
        <Link
          to={`/feeds/${feed.slug}`}
          className={`block w-12 h-12 shrink-0 overflow-hidden rounded-md ${focusRing}`}
        >
          <Artwork
            src={artworkUrl}
            alt=""
            loading="lazy"
            className="w-full h-full object-cover"
          />
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <h2 id={headingId} className="text-base font-semibold text-foreground truncate">
              <Link to={`/feeds/${feed.slug}`} className={`hover:text-primary ${focusRing}`}>
                {feedDisplayTitle(feed)}
              </Link>
            </h2>
            <FeedTypeBadge feedType={feed.feedType} />
          </div>
          <p className="text-xs text-muted-foreground">{feed.episodeCount} episodes</p>
        </div>
        <Link
          to={`/feeds/${feed.slug}`}
          className={`shrink-0 text-sm text-primary hover:underline ${focusRing}`}
        >
          View all episodes
        </Link>
      </div>
      {episodes.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No episodes yet</p>
      ) : (
        <div className="p-3 flex flex-col gap-2">
          {episodes.map((summary) => (
            <EpisodeRow
              key={summary.id}
              episode={toEpisode(summary)}
              feedSlug={feed.slug}
              feedArtworkUrl={feed.artworkUrl}
              selected={false}
              renderActions={() => (
                <EpisodeRowActions
                  feedSlug={feed.slug}
                  episodeId={summary.id}
                  status={summary.status}
                  jobState={summary.jobState}
                />
              )}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default DashboardEpisodeGroups;
