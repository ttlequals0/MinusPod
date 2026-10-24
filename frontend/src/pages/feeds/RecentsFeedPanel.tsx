import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateFeed, uploadFeedArtwork } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import { useDraftField } from '../../hooks/useDraftField';
import { btnBase, btnPrimary } from '../../components/buttonStyles';
import { inputBase, textareaBase, labelBase, fileInputBase } from '../../components/fieldStyles';

// Title, description and artwork are the only editable parts of the recents feed.
function RecentsFeedPanel({ feed, slug }: { feed: Feed; slug: string }) {
  const queryClient = useQueryClient();
  // Draft fields, so a background refetch does not overwrite an unsaved edit.
  const titleField = useDraftField(feed, (f) => f.title);
  const descriptionField = useDraftField(feed, (f) => f.description ?? '');
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    queryClient.invalidateQueries({ queryKey: ['feeds'] });
  };
  const save = useMutation({
    mutationFn: () => updateFeed(slug, {
      title: titleField.value.trim(), description: descriptionField.value,
    }),
    onSuccess: () => {
      titleField.markClean(titleField.value);
      descriptionField.markClean(descriptionField.value);
      invalidate();
    },
  });
  const artwork = useMutation({
    mutationFn: (file: File) => uploadFeedArtwork(slug, file),
    onSuccess: invalidate,
  });
  const error = save.error ?? artwork.error;
  const cutoff = feed.createdAt?.slice(0, 10);
  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Recents feed"
        subtitle={`Every episode processed on this instance and published on or after ${cutoff} appears here.`}
        storageKey="feed-section-recents"
        unmountWhenClosed
      >
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            From all your podcasts. Older episodes stay out even when they are reprocessed.
          </p>
          <label className="block">
            <span className={labelBase}>Title</span>
            <input aria-label="Feed title" value={titleField.value}
              onChange={(e) => titleField.setValue(e.target.value)}
              className={`w-full ${inputBase}`} />
          </label>
          <label className="block">
            <span className={labelBase}>Description</span>
            <textarea aria-label="Feed description" rows={3} value={descriptionField.value}
              onChange={(e) => descriptionField.setValue(e.target.value)}
              className={`w-full ${textareaBase}`} />
          </label>
          <div className="flex flex-wrap gap-2 items-center">
            <button type="button" onClick={() => save.mutate()} disabled={save.isPending || !titleField.value.trim()}
              className={`${btnBase} ${btnPrimary}`}>
              {save.isPending ? 'Saving...' : 'Save'}
            </button>
            {save.isSuccess && <span className="text-sm text-muted-foreground">Saved.</span>}
          </div>
          <div>
            <label htmlFor={`recents-artwork-${slug}`} className={labelBase}>Artwork</label>
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
        </div>
      </CollapsibleSection>
    </div>
  );
}

export default RecentsFeedPanel;
