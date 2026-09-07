import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateFeed, uploadFeedArtwork } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import { btnPrimary, btnSecondary } from '../../components/buttonStyles';
import { focusRing, inputBase } from '../../components/fieldStyles';

// Title, description and artwork are the only editable parts of the recents feed.
function RecentsFeedPanel({ feed, slug }: { feed: Feed; slug: string }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(feed.title);
  const [description, setDescription] = useState(feed.description ?? '');
  const since = feed.createdAt ? new Date(feed.createdAt).toLocaleDateString() : 'its creation';
  const save = useMutation({
    mutationFn: () => updateFeed(slug, { title: title.trim(), description }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed', slug] }),
  });
  const artwork = useMutation({
    mutationFn: (file: File) => uploadFeedArtwork(slug, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed', slug] }),
  });
  const error = save.error ?? artwork.error;
  return (
    <section className="bg-card rounded-lg border border-border p-4 sm:p-6 mb-6 space-y-4">
      <h2 className="text-lg font-semibold text-foreground">Recents feed</h2>
      <p className="text-sm text-muted-foreground">
        Every episode processed on this instance and published on or after {since} appears
        here, from all your podcasts. Older episodes stay out even when they are reprocessed.
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
        <label className={`px-4 py-2 rounded cursor-pointer ${btnSecondary} ${focusRing}`}>
          {artwork.isPending ? 'Uploading...' : 'Replace artwork'}
          <input type="file" accept="image/png,image/jpeg" className="sr-only"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) artwork.mutate(f); }} />
        </label>
        {save.isSuccess && !save.isPending && <span className="text-sm text-muted-foreground">Saved.</span>}
      </div>
      {error && <p className="text-sm text-destructive">{getErrorMessage(error)}</p>}
    </section>
  );
}

export default RecentsFeedPanel;
