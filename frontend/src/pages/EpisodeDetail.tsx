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
    const totalSecs = Math.floor(seconds);
    const hours = Math.floor(totalSecs / 3600);
    const minutes = Math.floor((totalSecs % 3600) / 60);
    const secs = totalSecs % 60;
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

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return '';
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(1)} MB`;
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

      <div className="bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
        <div className="flex gap-4">
          <div className="w-16 h-16 sm:w-24 sm:h-24 flex-shrink-0">
            <img
              src={`/api/v1/feeds/${slug}/artwork`}
              alt="Podcast artwork"
              className="w-full h-full object-cover rounded-lg"
              onError={(e) => {
                (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%239ca3af"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>';
              }}
            />
          </div>
          <div className="flex flex-col gap-2 min-w-0">
            <h1 className="text-xl sm:text-2xl font-bold text-foreground">{episode.title}</h1>
            <div className="flex flex-wrap items-center gap-2 sm:gap-4 text-sm text-muted-foreground">
              <span>{new Date(episode.published).toLocaleDateString()}</span>
              {episode.status === 'completed' && episode.newDuration ? (
                <span>{formatDuration(episode.newDuration)}</span>
              ) : episode.duration ? (
                <span>{formatDuration(episode.duration)}</span>
              ) : null}
              {episode.fileSize && (
                <span>{formatFileSize(episode.fileSize)}</span>
              )}
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[episode.status]}`}>
                {episode.status}
              </span>
              <button
                onClick={() => reprocessMutation.mutate()}
                disabled={reprocessMutation.isPending || episode.status === 'processing'}
                className="px-2 py-0.5 text-xs sm:text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {reprocessMutation.isPending ? 'Reprocessing...' : 'Reprocess'}
              </button>
            </div>
          </div>
        </div>

        {episode.status === 'completed' && (
          <div className="mt-4 pt-4 border-t border-border">
            <audio controls className="w-full" src={`/episodes/${slug}/${episode.id}.mp3`}>
              Your browser does not support the audio element.
            </audio>
          </div>
        )}

        {episode.description && (
          <p className="mt-4 text-muted-foreground whitespace-pre-wrap">
            {episode.description
              .replace(/<br\s*\/?>/gi, '\n')
              .replace(/<\/p>/gi, '\n')
              .replace(/<\/li>/gi, '\n')
              .replace(/<li>/gi, '- ')
              .replace(/<[^>]*>/g, '')
              .replace(/\n([ \t]*\n)+/g, '\n')
              .trim()}
          </p>
        )}
      </div>

      {episode.adMarkers && episode.adMarkers.length > 0 && (
        <div className="bg-card rounded-lg border border-border p-6 mb-6">
          <h2 className="text-xl font-semibold text-foreground mb-4">
            Detected Ads ({episode.adMarkers.length})
            {(episode.adsRemovedFirstPass !== undefined && episode.adsRemovedSecondPass !== undefined && episode.adsRemovedSecondPass > 0) && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                ({episode.adsRemovedFirstPass} first pass, {episode.adsRemovedSecondPass} second pass)
              </span>
            )}
            {episode.timeSaved && episode.timeSaved > 0 && (
              <span className="ml-2 text-base font-normal text-muted-foreground">
                - {formatDuration(episode.timeSaved)} time saved
              </span>
            )}
          </h2>
          <div className="space-y-3">
            {episode.adMarkers.map((segment, index) => (
              <div
                key={index}
                className="flex items-center justify-between p-3 bg-secondary/50 rounded-lg"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm">
                    {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                  </span>
                  {segment.pass && (
                    <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                      segment.pass === 1
                        ? 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
                        : segment.pass === 2
                        ? 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
                        : 'bg-green-500/20 text-green-600 dark:text-green-400'
                    }`}>
                      {segment.pass === 'merged' ? 'Merged' : `Pass ${segment.pass}`}
                    </span>
                  )}
                </div>
                <div className="flex flex-col items-end">
                  <span className="text-sm text-muted-foreground">
                    {Math.round(segment.confidence * 100)}% confidence
                  </span>
                  {segment.reason && (
                    <p className="text-sm text-muted-foreground mt-1 text-right max-w-md">{segment.reason}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {episode.rejectedAdMarkers && episode.rejectedAdMarkers.length > 0 && (
        <div className="bg-card rounded-lg border border-border p-6 mb-6">
          <h2 className="text-xl font-semibold text-foreground mb-4">
            Rejected Detections ({episode.rejectedAdMarkers.length})
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              - kept in audio
            </span>
          </h2>
          <p className="text-sm text-muted-foreground mb-4">
            These detections were flagged but not removed due to validation failures.
          </p>
          <div className="space-y-3">
            {episode.rejectedAdMarkers.map((segment, index) => (
              <div
                key={index}
                className="flex items-center justify-between p-3 bg-red-500/10 rounded-lg border border-red-500/20"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm">
                    {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                  </span>
                  <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-red-500/20 text-red-600 dark:text-red-400">
                    Rejected
                  </span>
                </div>
                <div className="flex flex-col items-end">
                  <span className="text-sm text-muted-foreground">
                    {Math.round(segment.confidence * 100)}% confidence
                  </span>
                  {segment.validation?.flags && segment.validation.flags.length > 0 && (
                    <p className="text-sm text-red-500 dark:text-red-400 mt-1 text-right max-w-md">
                      {segment.validation.flags.join(', ')}
                    </p>
                  )}
                  {segment.reason && (
                    <p className="text-sm text-muted-foreground mt-1 text-right max-w-md">{segment.reason}</p>
                  )}
                </div>
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
