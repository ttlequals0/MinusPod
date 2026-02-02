import { useState, useRef, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getEpisode, reprocessEpisode, regenerateChapters } from '../api/feeds';
import { submitCorrection } from '../api/patterns';
import LoadingSpinner from '../components/LoadingSpinner';
import TranscriptEditor, { AdCorrection } from '../components/TranscriptEditor';
import PatternLink from '../components/PatternLink';

// Save status type for visual feedback
type SaveStatus = 'idle' | 'saving' | 'success' | 'error';

function EpisodeDetail() {
  const { slug, episodeId } = useParams<{ slug: string; episodeId: string }>();
  const [showEditor, setShowEditor] = useState(false);
  const [jumpToTime, setJumpToTime] = useState<number | null>(null);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [showReprocessMenu, setShowReprocessMenu] = useState(false);
  const [editorSelectedAdIndex, setEditorSelectedAdIndex] = useState(0);
  const [savedScrollY, setSavedScrollY] = useState<number | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);

  const queryClient = useQueryClient();

  const { data: episode, isLoading, error } = useQuery({
    queryKey: ['episode', slug, episodeId],
    queryFn: () => getEpisode(slug!, episodeId!),
    enabled: !!slug && !!episodeId,
  });

  const reprocessMutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full') => reprocessEpisode(slug!, episodeId!, mode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
      setShowReprocessMenu(false);
    },
  });

  const regenerateChaptersMutation = useMutation({
    mutationFn: () => regenerateChapters(slug!, episodeId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    },
  });

  // Mutation for submitting ad corrections
  const correctionMutation = useMutation({
    mutationFn: (correction: AdCorrection) =>
      submitCorrection(slug!, episodeId!, {
        type: correction.type,
        original_ad: {
          start: correction.originalAd.start,
          end: correction.originalAd.end,
          pattern_id: correction.originalAd.pattern_id,
          confidence: correction.originalAd.confidence,
          reason: correction.originalAd.reason,
        },
        adjusted_start: correction.adjustedStart,
        adjusted_end: correction.adjustedEnd,
      }),
    onMutate: () => {
      setSaveStatus('saving');
    },
    onSuccess: () => {
      setSaveStatus('success');
      setTimeout(() => setSaveStatus('idle'), 2000);
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    },
    onError: (error) => {
      console.error('Failed to save correction:', error);
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 3000);
    },
  });

  // Handle ad corrections from TranscriptEditor
  const handleCorrection = (correction: AdCorrection) => {
    correctionMutation.mutate(correction);
  };

  // Jump to a specific ad in the editor
  const handleJumpToAd = (startTime: number) => {
    setJumpToTime(startTime);
    if (!showEditor) {
      setShowEditor(true);
    }
    // Scroll to editor
    setTimeout(() => {
      editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  };

  // Convert ad markers to TranscriptEditor format - memoized to prevent stale closures in editor
  const detectedAds = useMemo(() => {
    if (!episode?.adMarkers) return [];
    return episode.adMarkers.map((marker) => ({
      start: marker.start,
      end: marker.end,
      confidence: marker.confidence,
      reason: marker.reason || '',
      sponsor: undefined, // Could be extracted from reason if needed
      pattern_id: undefined,
      detection_stage: marker.pass === 1 ? 'first_pass' : marker.pass === 2 ? 'second_pass' : 'merged',
    }));
  }, [episode?.adMarkers]);

  // Generate approximate transcript segments from plain text - memoized for performance
  // This is a placeholder - ideally we'd have timestamped segments from the API
  const transcriptSegments = useMemo(() => {
    if (!episode?.transcript || !episode.originalDuration) return [];

    // Split transcript into sentences/chunks
    const text = episode.transcript;
    const sentences = text.split(/(?<=[.!?])\s+/).filter((s) => s.trim());

    if (sentences.length === 0) return [];

    // Distribute timestamps evenly across sentences (approximation)
    const duration = episode.originalDuration;
    const segmentDuration = duration / sentences.length;

    return sentences.map((sentence, index) => ({
      start: index * segmentDuration,
      end: (index + 1) * segmentDuration,
      text: sentence.trim(),
    }));
  }, [episode?.transcript, episode?.originalDuration]);

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

  // Helper to find correction for an ad marker
  const getAdCorrection = (start: number, end: number) => {
    return episode?.corrections?.find(c =>
      c.original_bounds.start === start && c.original_bounds.end === end
    );
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
              {episode.transcriptVttAvailable && (
                <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                  VTT
                </span>
              )}
              {episode.chaptersAvailable && (
                <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-500/20 text-purple-600 dark:text-purple-400">
                  Chapters
                </span>
              )}
              <div className="relative">
                <button
                  onClick={() => setShowReprocessMenu(!showReprocessMenu)}
                  disabled={reprocessMutation.isPending || episode.status === 'processing'}
                  className="px-2 py-0.5 text-xs sm:text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                >
                  {reprocessMutation.isPending ? 'Reprocessing...' : 'Reprocess'}
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {showReprocessMenu && !reprocessMutation.isPending && episode.status !== 'processing' && (
                  <div className="absolute top-full right-0 mt-1 w-52 bg-card border border-border rounded-lg shadow-lg z-10">
                    <button
                      onClick={() => reprocessMutation.mutate('reprocess')}
                      className="w-full px-3 py-2 text-left text-sm hover:bg-accent rounded-t-lg"
                      title="Use learned patterns + Claude analysis"
                    >
                      <div className="font-medium">Reprocess</div>
                      <div className="text-xs text-muted-foreground">Use patterns + Claude</div>
                    </button>
                    <button
                      onClick={() => reprocessMutation.mutate('full')}
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-accent border-t border-border ${!episode.transcriptVttAvailable ? 'rounded-b-lg' : ''}`}
                      title="Skip pattern DB, Claude analyzes everything fresh"
                    >
                      <div className="font-medium">Full Analysis</div>
                      <div className="text-xs text-muted-foreground">Skip patterns, Claude only</div>
                    </button>
                    {episode.transcriptVttAvailable && (
                      <button
                        onClick={() => {
                          regenerateChaptersMutation.mutate();
                          setShowReprocessMenu(false);
                        }}
                        disabled={regenerateChaptersMutation.isPending}
                        className="w-full px-3 py-2 text-left text-sm hover:bg-accent rounded-b-lg border-t border-border disabled:opacity-50"
                        title="Regenerate chapters from existing transcript"
                      >
                        <div className="font-medium">Regenerate Chapters</div>
                        <div className="text-xs text-muted-foreground">Use existing transcript</div>
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {episode.status === 'completed' && (
          <div className="mt-4 pt-4 border-t border-border">
            <audio controls className="w-full" src={`/episodes/${slug}/${episode.id}.mp3`}>
              Your browser does not support the audio element.
            </audio>
            {(episode.transcriptVttAvailable || episode.chaptersAvailable) && (
              <div className="flex flex-wrap gap-2 mt-3">
                {episode.transcriptVttAvailable && episode.transcriptVttUrl && (
                  <a
                    href={episode.transcriptVttUrl}
                    download
                    className="px-3 py-1 text-sm bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded hover:bg-blue-500/30 transition-colors"
                  >
                    Download VTT
                  </a>
                )}
                {episode.chaptersAvailable && episode.chaptersUrl && (
                  <a
                    href={episode.chaptersUrl}
                    download
                    className="px-3 py-1 text-sm bg-purple-500/20 text-purple-600 dark:text-purple-400 rounded hover:bg-purple-500/30 transition-colors"
                  >
                    Download Chapters
                  </a>
                )}
              </div>
            )}
          </div>
        )}

        {episode.description && (
          <p className="mt-4 text-muted-foreground whitespace-pre-wrap break-words">
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
          <div className="mb-4">
            {/* Row 1: Title + Edit button */}
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-foreground">
                Detected Ads ({episode.adMarkers.length})
              </h2>
              {episode.status === 'completed' && episode.transcript && (
                <button
                  onClick={() => {
                    if (!showEditor) {
                      setSavedScrollY(window.scrollY);
                    }
                    setShowEditor(!showEditor);
                  }}
                  className="flex items-center gap-2 px-3 py-1.5 text-sm bg-secondary text-secondary-foreground rounded-md hover:bg-secondary/80 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                  {showEditor ? 'Hide Editor' : 'Edit Ads'}
                </button>
              )}
            </div>
            {/* Row 2: Pass info + time saved */}
            {((episode.adsRemovedFirstPass !== undefined && episode.adsRemovedSecondPass !== undefined && episode.adsRemovedSecondPass > 0) || (episode.timeSaved && episode.timeSaved > 0)) && (
              <div className="mt-1 text-sm text-muted-foreground">
                {(episode.adsRemovedFirstPass !== undefined && episode.adsRemovedSecondPass !== undefined && episode.adsRemovedSecondPass > 0) && (
                  <span>{episode.adsRemovedFirstPass} first pass, {episode.adsRemovedSecondPass} second pass</span>
                )}
                {episode.timeSaved && episode.timeSaved > 0 && (
                  <span className={episode.adsRemovedSecondPass && episode.adsRemovedSecondPass > 0 ? 'ml-2' : ''}>
                    {episode.adsRemovedSecondPass && episode.adsRemovedSecondPass > 0 ? '- ' : ''}{formatDuration(episode.timeSaved)} time saved
                  </span>
                )}
              </div>
            )}
          </div>

          {/* TranscriptEditor for reviewing/editing ad detections */}
          {showEditor && episode.status === 'completed' && (
            <div className="mb-4" ref={editorRef}>
              <TranscriptEditor
                segments={transcriptSegments}
                detectedAds={detectedAds}
                audioUrl={`/episodes/${slug}/${episode.id}.mp3`}
                onCorrection={handleCorrection}
                onClose={() => {
                  setShowEditor(false);
                  if (savedScrollY !== null) {
                    setTimeout(() => window.scrollTo(0, savedScrollY), 0);
                    setSavedScrollY(null);
                  }
                }}
                initialSeekTime={jumpToTime ?? undefined}
                saveStatus={saveStatus}
                selectedAdIndex={editorSelectedAdIndex}
                onSelectedAdIndexChange={setEditorSelectedAdIndex}
              />
            </div>
          )}

          <div className="space-y-3">
            {episode.adMarkers.map((segment, index) => (
              <div
                key={index}
                className="p-3 bg-secondary/50 rounded-lg"
              >
                {/* Row 1: Time, badges, jump button, confidence */}
                <div className="flex flex-wrap items-center gap-2">
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
                  {episode.transcript && (
                    <button
                      onClick={() => handleJumpToAd(segment.start)}
                      className="px-3 py-1.5 sm:px-2 sm:py-0.5 text-xs bg-primary/10 text-primary rounded hover:bg-primary/20 active:bg-primary/30 transition-colors touch-manipulation min-h-[36px] sm:min-h-0"
                      title="Jump to this ad in editor"
                    >
                      Jump
                    </button>
                  )}
                  {(() => {
                    const correction = getAdCorrection(segment.start, segment.end);
                    if (correction) {
                      return (
                        <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                          correction.correction_type === 'confirm'
                            ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                            : correction.correction_type === 'false_positive'
                            ? 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400'
                            : 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
                        }`}>
                          {correction.correction_type === 'confirm' ? 'Confirmed'
                           : correction.correction_type === 'false_positive' ? 'Not Ad'
                           : 'Adjusted'}
                        </span>
                      );
                    }
                    return null;
                  })()}
                  <span className="ml-auto text-sm text-muted-foreground whitespace-nowrap">
                    {Math.round(segment.confidence * 100)}% confidence
                  </span>
                </div>
                {/* Row 2: Description - full width below badges for better mobile display */}
                {segment.reason && (
                  <p className="text-sm text-muted-foreground mt-2 break-words">
                    <PatternLink reason={segment.reason} />
                  </p>
                )}
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
                className="p-3 bg-red-500/10 rounded-lg border border-red-500/20"
              >
                {(() => {
                  const correction = getAdCorrection(segment.start, segment.end);
                  return (
                    <>
                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-sm">
                            {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                          </span>
                          <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-red-500/20 text-red-600 dark:text-red-400">
                            Rejected
                          </span>
                          {correction && (
                            <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                              correction.correction_type === 'confirm'
                                ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                                : 'bg-yellow-500/20 text-yellow-600 dark:text-yellow-400'
                            }`}>
                              {correction.correction_type === 'confirm' ? 'Confirmed' : 'Not Ad'}
                            </span>
                          )}
                        </div>
                        <span className="text-sm text-muted-foreground">
                          {Math.round(segment.confidence * 100)}% confidence
                        </span>
                      </div>
                      {segment.validation?.flags && segment.validation.flags.length > 0 && (
                        <p className="text-sm text-red-500 dark:text-red-400 mt-2">
                          {segment.validation.flags.join(', ')}
                        </p>
                      )}
                      {segment.reason && (
                        <p className="text-sm text-muted-foreground mt-1">{segment.reason}</p>
                      )}
                      {!correction && (
                        <div className="flex flex-col sm:flex-row gap-2 mt-3">
                          <button
                            onClick={() => handleCorrection({
                              type: 'confirm',
                              originalAd: {
                                start: segment.start,
                                end: segment.end,
                                confidence: segment.confidence,
                                reason: segment.reason || '',
                              },
                            })}
                            disabled={correctionMutation.isPending}
                            className={`flex-1 sm:flex-none px-3 py-2 sm:py-1.5 text-sm sm:text-xs rounded disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0 ${
                              saveStatus === 'success' ? 'bg-green-700 text-white' :
                              saveStatus === 'error' ? 'bg-red-600 text-white' :
                              'bg-green-600 hover:bg-green-700 active:bg-green-800 text-white'
                            }`}
                          >
                            {saveStatus === 'saving' ? 'Saving...' :
                             saveStatus === 'success' ? 'Saved!' :
                             saveStatus === 'error' ? 'Error!' :
                             'Confirm as Ad'}
                          </button>
                          <button
                            onClick={() => handleCorrection({
                              type: 'reject',
                              originalAd: {
                                start: segment.start,
                                end: segment.end,
                                confidence: segment.confidence,
                                reason: segment.reason || '',
                              },
                            })}
                            disabled={correctionMutation.isPending}
                            className={`flex-1 sm:flex-none px-3 py-2 sm:py-1.5 text-sm sm:text-xs rounded disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0 ${
                              saveStatus === 'success' ? 'bg-green-700 text-white' :
                              saveStatus === 'error' ? 'bg-red-600 text-white' :
                              'bg-destructive hover:bg-destructive/90 active:bg-destructive/80 text-destructive-foreground'
                            }`}
                          >
                            {saveStatus === 'saving' ? 'Saving...' :
                             saveStatus === 'success' ? 'Saved!' :
                             saveStatus === 'error' ? 'Error!' :
                             'Not an Ad'}
                          </button>
                        </div>
                      )}
                    </>
                  );
                })()}
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
