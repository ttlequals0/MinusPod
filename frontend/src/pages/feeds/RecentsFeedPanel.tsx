import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateFeed, uploadFeedArtwork } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import { btnPrimary } from '../../components/buttonStyles';
import { fileInputBase, focusRing, inputBase } from '../../components/fieldStyles';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { formatDate } from '../../utils/format';

// Title, description and artwork are the only editable parts of the recents feed.
function RecentsFeedPanel({ feed, slug }: { feed: Feed; slug: string }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(feed.title);
  const [description, setDescription] = useState(feed.description ?? '');
  useSyncFromQuery(feed, (f) => { setTitle(f.title); setDescription(f.description ?? ''); });
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    queryClient.invalidateQueries({ queryKey: ['feeds'] });
  };
  const save = useMutation({
    mutationFn: () => updateFeed(slug, { title: title.trim(), description }),
    onSuccess: invalidate,
  });
  const artwork = useMutation({
    mutationFn: (file: File) => uploadFeedArtwork(slug, file),
    onSuccess: invalidate,
  });
  const error = save.error ?? artwork.error;
  return (
    <section className="bg-card rounded-lg border border-border p-4 sm:p-6 mb-6 space-y-4">
      <h2 className="text-lg font-semibold text-foreground">Recents feed</h2>
      <p className="text-sm text-muted-foreground">
        Every episode processed on this instance and published on or after {formatDate(feed.createdAt ?? null)}
        appears here, from all your podcasts. Older episodes stay out even when they are reprocessed.
      </p>
      <label className="block text-sm">
        <span className="font-medium text-foreground">Title</span>
        <input aria-label="Feed title" value={title} onChange={(e) => setTitle(e.target.value)}
          className={`mt-1 w-full ${inputBase}`} />
      </label>
      <label className="block text-sm">
        <span className="font-medium text-foreground">Description</span>
        <textarea aria-label="Feed description" rows={3} value={description}
          onChange={(e) => setDescription(e.target.value)} className={`mt-1 w-full ${inputBase}`} />
      </label>
      <div className="flex flex-wrap gap-2 items-center">
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending || !title.trim()}
          className={`px-4 py-2 rounded ${btnPrimary} disabled:opacity-50 ${focusRing}`}>
          {save.isPending ? 'Saving...' : 'Save'}
        </button>
        {save.isSuccess && <span className="text-sm text-muted-foreground">Saved.</span>}
      </div>
      <div>
        <label htmlFor={`recents-artwork-${slug}`} className="block text-sm font-medium text-foreground mb-2">Artwork</label>
        <input
          id={`recents-artwork-${slug}`}
          type="file"
          accept="image/jpeg,image/png"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) artwork.mutate(file);
          }}
          className={fileInputBase}
        />
        {artwork.isPending && <p className="mt-1 text-sm text-muted-foreground">Uploading...</p>}
        {artwork.isSuccess && <p className="mt-1 text-sm text-success">Artwork updated.</p>}
      </div>
      {error && <p className="text-sm text-destructive">{getErrorMessage(error)}</p>}
    </section>
  );
}

export default RecentsFeedPanel;
