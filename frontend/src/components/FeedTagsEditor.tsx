import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getFeedTags,
  setFeedUserTags,
  getTagVocabulary,
  type FeedTagBreakdown,
} from '../api/community';
import { TagChips } from './TagChips';
import LoadingSpinner from './LoadingSpinner';

interface Props {
  slug: string;
}

export function FeedTagsEditor({ slug }: Props) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);

  const { data: tags, isLoading } = useQuery({
    queryKey: ['feedTags', slug],
    queryFn: () => getFeedTags(slug),
  });

  const { data: vocab } = useQuery({
    queryKey: ['tagVocabulary'],
    queryFn: getTagVocabulary,
    // Vocabulary ships with the app image; a runtime change would require a
    // restart, which blows the React Query cache anyway. Infinity = one fetch
    // per page load, no refresh-on-focus.
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const save = useMutation({
    mutationFn: (userTags: string[]) => setFeedUserTags(slug, userTags),
    onSuccess: (next) => {
      qc.setQueryData<FeedTagBreakdown>(['feedTags', slug], next);
    },
  });

  if (isLoading || !tags) {
    return (
      <div className="bg-card rounded-lg border border-border p-4 mb-6">
        <h2 className="text-sm font-medium text-foreground mb-2">Tags</h2>
        <LoadingSpinner className="py-4" />
      </div>
    );
  }

  const userSet = new Set(tags.user);
  const rssSet = new Set(tags.rss);
  const episodeSet = new Set(tags.episode);
  const effective = tags.effective;

  const remainingVocab = (vocab?.all_tags || []).filter((t) => !userSet.has(t) && !rssSet.has(t) && !episodeSet.has(t));

  function addTag(tag: string) {
    if (!tags) return;
    save.mutate([...tags.user, tag].filter((t, i, a) => a.indexOf(t) === i));
    setAdding(false);
  }

  function removeTag(tag: string) {
    if (!tags) return;
    save.mutate(tags.user.filter((t) => t !== tag));
  }

  return (
    <div className="bg-card rounded-lg border border-border p-4 mb-6">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 className="text-sm font-medium text-foreground">Tags</h2>
        <span className="text-xs text-muted-foreground">
          Used to filter community ad patterns. Tags from RSS metadata and episodes are automatic; you can also add or remove your own below.
        </span>
      </div>

      {effective.length === 0 && (
        <p className="text-sm text-muted-foreground mb-3">
          No tags yet. The next RSS refresh will populate iTunes categories automatically, or add one below.
        </p>
      )}

      {effective.length > 0 && (
        <div className="space-y-2 mb-3">
          {tags.rss.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground w-16 shrink-0">From RSS:</span>
              <TagChips tags={tags.rss} />
            </div>
          )}
          {tags.episode.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground w-16 shrink-0">Episodes:</span>
              <TagChips tags={tags.episode} />
            </div>
          )}
          {tags.user.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground w-16 shrink-0">Yours:</span>
              <div className="flex flex-wrap gap-1">
                {tags.user.map((t) => (
                  <span
                    key={t}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded bg-blue-500/15 text-blue-700 dark:text-blue-400"
                  >
                    {t}
                    <button
                      type="button"
                      onClick={() => removeTag(t)}
                      disabled={save.isPending}
                      className="text-blue-700/60 dark:text-blue-400/60 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
                      aria-label={`Remove ${t}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-2">
        {!adding ? (
          <button
            type="button"
            onClick={() => setAdding(true)}
            disabled={save.isPending || remainingVocab.length === 0}
            className="px-2 py-1 text-xs rounded border border-border hover:bg-accent disabled:opacity-50"
          >
            + Add tag
          </button>
        ) : (
          <>
            <select
              autoFocus
              defaultValue=""
              onChange={(e) => e.target.value && addTag(e.target.value)}
              className="px-2 py-1 text-xs bg-secondary border border-border rounded"
            >
              <option value="" disabled>Pick a tag…</option>
              {vocab?.podcast_genres && (
                <optgroup label="Podcast genres">
                  {vocab.podcast_genres
                    .filter((e) => !userSet.has(e.tag) && !rssSet.has(e.tag) && !episodeSet.has(e.tag))
                    .map((e) => (
                      <option key={e.tag} value={e.tag} title={e.description}>{e.tag}</option>
                    ))}
                </optgroup>
              )}
              {vocab?.sponsor_industries && (
                <optgroup label="Sponsor industries">
                  {vocab.sponsor_industries
                    .filter((e) => !userSet.has(e.tag) && !rssSet.has(e.tag) && !episodeSet.has(e.tag))
                    .map((e) => (
                      <option key={e.tag} value={e.tag} title={e.description}>{e.tag}</option>
                    ))}
                </optgroup>
              )}
            </select>
            <button
              type="button"
              onClick={() => setAdding(false)}
              className="px-2 py-1 text-xs rounded border border-border hover:bg-accent"
            >
              Cancel
            </button>
          </>
        )}
        {save.isError && (
          <span className="text-xs text-red-600 dark:text-red-400">
            {(save.error as Error)?.message || 'Save failed'}
          </span>
        )}
      </div>
    </div>
  );
}
