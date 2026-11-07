import { useMutation, useQueryClient } from '@tanstack/react-query';
import { uploadFeedArtwork } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import { fileInputBase, labelBase } from '../../components/fieldStyles';
import { IS_DEMO } from '../../demo/flag';
import { DEMO_WRITE_TOOLTIP } from '../../demo/copy';

function FeedArtworkUpload({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const artworkMutation = useMutation({
    mutationFn: (file: File) => uploadFeedArtwork(slug, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
  });

  const fileInput = (
    <input
      id={`local-artwork-${slug}`}
      type="file"
      accept="image/jpeg,image/png"
      disabled={artworkMutation.isPending || IS_DEMO}
      aria-invalid={artworkMutation.isError}
      aria-describedby={artworkMutation.isError ? `artwork-error-${slug}` : undefined}
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (file) artworkMutation.mutate(file);
      }}
      className={fileInputBase}
    />
  );

  return (
    <div>
      <label htmlFor={`local-artwork-${slug}`} className={labelBase}>Artwork</label>
      {IS_DEMO ? <span className="inline-flex cursor-not-allowed" title={DEMO_WRITE_TOOLTIP}>{fileInput}</span> : fileInput}
      {artworkMutation.isPending && <p role="status" className="mt-1 text-sm text-muted-foreground">Uploading artwork...</p>}
      {artworkMutation.isSuccess && <p role="status" className="mt-1 text-sm text-success">Artwork updated.</p>}
      {artworkMutation.isError && (
        <p id={`artwork-error-${slug}`} role="alert" className="mt-1 text-sm text-destructive">{getErrorMessage(artworkMutation.error, 'Could not upload artwork.')}</p>
      )}
    </div>
  );
}

export default FeedArtworkUpload;
