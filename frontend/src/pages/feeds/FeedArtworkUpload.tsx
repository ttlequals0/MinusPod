import { useMutation, useQueryClient } from '@tanstack/react-query';
import { uploadFeedArtwork } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import { fileInputBase, labelBase } from '../../components/fieldStyles';

function FeedArtworkUpload({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const artworkMutation = useMutation({
    mutationFn: (file: File) => uploadFeedArtwork(slug, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
  });

  return (
    <div>
      <label htmlFor={`local-artwork-${slug}`} className={labelBase}>Artwork</label>
      <input
        id={`local-artwork-${slug}`}
        type="file"
        accept="image/jpeg,image/png"
        disabled={artworkMutation.isPending}
        aria-invalid={artworkMutation.isError}
        aria-describedby={artworkMutation.isError ? `artwork-error-${slug}` : undefined}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) artworkMutation.mutate(file);
        }}
        className={fileInputBase}
      />
      {artworkMutation.isPending && <p role="status" className="mt-1 text-sm text-muted-foreground">Uploading artwork...</p>}
      {artworkMutation.isSuccess && <p role="status" className="mt-1 text-sm text-success">Artwork updated.</p>}
      {artworkMutation.isError && (
        <p id={`artwork-error-${slug}`} role="alert" className="mt-1 text-sm text-destructive">{getErrorMessage(artworkMutation.error, 'Could not upload artwork.')}</p>
      )}
    </div>
  );
}

export default FeedArtworkUpload;
