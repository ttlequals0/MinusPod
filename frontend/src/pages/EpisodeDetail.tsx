import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getEpisode, reprocessEpisode } from '../api/feeds';
import LoadingSpinner from '../components/LoadingSpinner';

function EpisodeDetail() {
  const { slug, episodeId } = useParams<{ slug: string; episodeId: string }>();

  const queryClient = useQueryClient();

  const { data: episode, isLoading, error } = useQuery({
    queryKey: ['episode', slug, episodeId],
    queryFn: () => getEpisode(slug!, episodeId!),
    enabled: !!slug && !!episodeId,
  });

  const reprocessMutation = useMutation({
    mutationFn: () => reprocessEpisode(slug!, episodeId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    },
  });

  const formatDuration = (seconds?: number) => {
    if (!seconds) return '';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours > 0) {
      return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  };

  const formatTimestamp = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    if (hours > 0) {
      return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  };

  if (isLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  if (error || !episode) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive">Failed to load episode</p>
        <Link to={`/feeds/${slug}`} className="text-primary hover:underline mt-2 inline-block">
          Back to Feed
        </Link>
      </div>
    );
  }

  const statusColors = {
    pending: 'bg-muted text-muted-foreground',
    processing: 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400',
    completed: 'bg-green-500/20 text-green-600 dark:text-green-400',
    failed: 'bg-destructive/20 text-destructive',
  };

  return (
    <div>
      <Link to={`/feeds/${slug}`} className="text-primary hover:underline mb-4 inline-block">
        Back to Feed
      </Link>

      <div className="bg-card rounded-lg border border-border p-6 mb-6">
        <div className="flex justify-between items-start gap-4">
          <div>
            <h1 className="text-2xl font-bold text-foreground">{episode.title}</h1>
            <div className="flex items-center gap-4 mt-2 text-sm text-muted-foreground">
              <span>{new Date(episode.published).toLocaleDateString()}</span>
              {episode.duration && <span>{formatDuration(episode.duration)}</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-3 py-1 rounded-full text-sm ${statusColors[episode.status]}`}>
              {episode.status}
            </span>
            <button
              onClick={() => reprocessMutation.mutate()}
              disabled={reprocessMutation.isPending || episode.status === 'processing'}
              className="px-3 py-1 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {reprocessMutation.isPending ? 'Reprocessing...' : 'Reprocess'}
            </button>
          </div>
        </div>

        {episode.description && (
          <p className="mt-4 text-muted-foreground">{episode.description}</p>
        )}

        {episode.processed_url && (
          <div className="mt-4 pt-4 border-t border-border">
            <audio controls className="w-full" src={episode.processed_url}>
              Your browser does not support the audio element.
            </audio>
          </div>
        )}
      </div>

      {episode.ad_segments && episode.ad_segments.length > 0 && (
        <div className="bg-card rounded-lg border border-border p-6 mb-6">
          <h2 className="text-xl font-semibold text-foreground mb-4">
            Detected Ads ({episode.ad_segments.length})
          </h2>
          <div className="space-y-3">
            {episode.ad_segments.map((segment, index) => (
              <div
                key={index}
                className="flex items-center justify-between p-3 bg-secondary/50 rounded-lg"
              >
                <div>
                  <span className="font-mono text-sm">
                    {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                  </span>
                  {segment.reason && (
                    <p className="text-sm text-muted-foreground mt-1">{segment.reason}</p>
                  )}
                </div>
                <span className="text-sm text-muted-foreground">
                  {Math.round(segment.confidence * 100)}% confidence
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {episode.transcript && (
        <div className="bg-card rounded-lg border border-border p-6">
          <h2 className="text-xl font-semibold text-foreground mb-4">Transcript</h2>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <pre className="whitespace-pre-wrap text-sm text-muted-foreground font-sans">
              {episode.transcript}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default EpisodeDetail;
