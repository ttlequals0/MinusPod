import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Play, Pause } from 'lucide-react';
import CollapsibleSection from './CollapsibleSection';
import { cueTypeLabel, type CueTemplateType } from '../api/cueTemplates';
import { setCueDetectionVerdict, type CueVerdict } from '../api/cueDetections';
import type { CueDetection } from '../api/types';

interface CueDetectionsSectionProps {
  slug: string;
  episodeId: string;
  detections: CueDetection[];
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

const OUTCOME_META: Record<CueDetection['outcome'], { label: string; className: string; title: string }> = {
  pair: {
    label: 'Paired',
    className: 'bg-violet-500/20 text-violet-600 dark:text-violet-400',
    title: 'Two cues bracketed and created an ad',
  },
  snap: {
    label: 'Snapped',
    className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400',
    title: 'Moved an ad edge onto this cue',
  },
  none: {
    label: 'LLM cue',
    className: 'bg-muted text-muted-foreground',
    title: 'Sent to the model as evidence; did not move an ad edge',
  },
};

function CueDetectionsSection({ slug, episodeId, detections }: CueDetectionsSectionProps) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: ({ id, verdict }: { id: number; verdict: CueVerdict }) =>
      setCueDetectionVerdict(id, verdict),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    },
  });

  const hasPending = detections.some((d) => d.verdict === 'pending');

  // Audition a match: one shared <audio> element plays the [start, end] window
  // of the original audio so only one row sounds at a time. A timeupdate
  // listener (held in a ref so it can be detached) pauses at the match end.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Holds a function that detaches whichever listener is active (the timeupdate
  // stop, or a pending loadedmetadata seek), so a row switch tears down cleanly.
  const cleanupRef = useRef<(() => void) | null>(null);
  const [playingId, setPlayingId] = useState<number | null>(null);
  const audioUrl = `/api/v1/feeds/${slug}/episodes/${episodeId}/original.mp3`;

  const clearStop = () => {
    if (cleanupRef.current) cleanupRef.current();
    cleanupRef.current = null;
  };

  const playMatch = (d: CueDetection) => {
    const audio = audioRef.current;
    if (!audio) return;
    clearStop();
    // Seek then play, but only once metadata is loaded -- setting currentTime
    // before HAVE_METADATA is dropped by Chrome/Safari and would play from 0:00.
    const begin = () => {
      audio.currentTime = d.start_s;
      const stop = () => {
        if (audio.currentTime >= d.end_s) { clearStop(); audio.pause(); }
      };
      audio.addEventListener('timeupdate', stop);
      cleanupRef.current = () => audio.removeEventListener('timeupdate', stop);
      // Rely on onPause/onEnded to clear playingId; a no-op catch avoids an
      // aborted play() (rapid row switch) clobbering another row's state.
      audio.play().then(() => setPlayingId(d.id)).catch(() => {});
    };
    if (audio.readyState >= 1) {
      begin();
    } else {
      audio.addEventListener('loadedmetadata', begin, { once: true });
      cleanupRef.current = () => audio.removeEventListener('loadedmetadata', begin);
    }
  };

  const toggleMatch = (d: CueDetection) => {
    if (playingId === d.id) {
      clearStop();
      audioRef.current?.pause();
    } else {
      playMatch(d);
    }
  };

  useEffect(() => () => { clearStop(); audioRef.current?.pause(); }, []);

  return (
    <CollapsibleSection
      title="Cue Matches"
      subtitle="Confirm or reject each template cue match"
      defaultOpen={hasPending}
      storageKey={`episode-cue-detections-${episodeId}`}
      headerRight={
        <span className="px-2 py-0.5 text-xs rounded-full bg-secondary text-secondary-foreground">
          {detections.length}
        </span>
      }
    >
      <p className="text-sm text-muted-foreground mb-4">
        Confirm a match on a real ad boundary, or reject a false one. Verdicts tune the feed's cues; they never add or remove ads.
      </p>
      <div className="space-y-2">
        {detections.map((d) => {
          const outcome = OUTCOME_META[d.outcome];
          const pending = mutation.isPending && mutation.variables?.id === d.id;
          return (
            <div
              key={d.id}
              className="p-3 bg-secondary/40 rounded-lg border border-border"
            >
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => toggleMatch(d)}
                    aria-label={playingId === d.id ? 'Pause match' : 'Play this match'}
                    title={playingId === d.id ? 'Pause' : 'Play this match'}
                    className="p-1.5 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shrink-0 touch-manipulation"
                  >
                    {playingId === d.id ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                  </button>
                  <span className="font-mono text-sm text-foreground">
                    {formatTime(d.start_s)} - {formatTime(d.end_s)}
                  </span>
                  {d.cue_type && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground">
                      {cueTypeLabel(d.cue_type as CueTemplateType)}
                    </span>
                  )}
                  <span
                    title={outcome.title}
                    className={`px-1.5 py-0.5 text-xs rounded font-medium ${outcome.className}`}
                  >
                    {outcome.label}
                  </span>
                  {d.match_score != null && (
                    <span className="text-xs text-muted-foreground">
                      Match {Math.round(d.match_score * 100)}%
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {d.verdict === 'pending' ? (
                    <>
                      <button
                        onClick={() => mutation.mutate({ id: d.id, verdict: 'confirmed' })}
                        disabled={pending}
                        className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium bg-green-600 hover:bg-green-700 active:bg-green-800 text-white disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => mutation.mutate({ id: d.id, verdict: 'rejected' })}
                        disabled={pending}
                        className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium bg-destructive hover:bg-destructive/90 active:bg-destructive/80 text-destructive-foreground disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                      >
                        Reject
                      </button>
                    </>
                  ) : (
                    <>
                      <span
                        className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                          d.verdict === 'confirmed'
                            ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                            : 'bg-red-500/20 text-red-600 dark:text-red-400'
                        }`}
                      >
                        {d.verdict === 'confirmed' ? 'Confirmed' : 'Rejected'}
                      </span>
                      <button
                        onClick={() => mutation.mutate({ id: d.id, verdict: 'pending' })}
                        disabled={pending}
                        className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded border border-border text-muted-foreground hover:bg-secondary disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                      >
                        Reset
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <audio
        ref={audioRef}
        src={audioUrl}
        preload="metadata"
        onPause={() => setPlayingId(null)}
        onEnded={() => setPlayingId(null)}
      />
    </CollapsibleSection>
  );
}

export default CueDetectionsSection;
