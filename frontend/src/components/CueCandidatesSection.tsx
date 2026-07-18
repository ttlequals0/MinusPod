import { useState, useRef, useEffect } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import CollapsibleSection from './CollapsibleSection';
import LoadingSpinner from './LoadingSpinner';
import CueMarkModal from './CueMarkModal';
import {
  getCueCandidates, cueCandidateLabel,
  dismissCueCandidate, listCueCandidateDismissals, undoCueCandidateDismissal,
  type CueCandidate, type CueTemplateType,
} from '../api/cueTemplates';
import { episodeOriginalUrl } from '../api/feeds';
import { getSettings } from '../api/settings';
import { formatTimestamp } from '../utils/format';
import { useScanQuery } from '../hooks/useScanQuery';
import { btnPrimary, btnSecondary } from './buttonStyles';

interface CueCandidatesSectionProps {
  slug: string;
  episodeId: string;
  episodeTitle: string;
  episodeDuration: number;
  hasOriginalAudio: boolean;
}

const btnBase =
  'px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0';
const makeBtn = `${btnBase} ${btnPrimary}`;
const secondaryBtn = `${btnBase} ${btnSecondary}`;

function CueCandidatesSection({
  slug, episodeId, episodeTitle, episodeDuration, hasOriginalAudio,
}: CueCandidatesSectionProps) {
  const queryClient = useQueryClient();
  const [scanned, setScanned] = useState(false);
  const [seed, setSeed] = useState<
    { start: number; end: number; cueType?: CueTemplateType } | null
  >(null);

  // Inline preview: one shared <audio> plays just the candidate's [start, end]
  // span so a candidate can be heard without opening the capture modal.
  const audioRef = useRef<HTMLAudioElement>(null);
  const previewStopRef = useRef<(() => void) | null>(null);
  // Bumped on every preview start/stop so a slow cold-load callback for a
  // superseded candidate bails instead of arming a stray stop listener.
  const reqRef = useRef(0);
  const [playingKey, setPlayingKey] = useState<string | null>(null);

  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const captureMinSeconds = settingsQuery.data?.audioCueCaptureMinSeconds?.value ?? 0.2;
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 10;
  const captureMaxIntroSeconds = settingsQuery.data?.audioCueCaptureMaxIntroSeconds?.value ?? 60;
  const captureMaxOutroSeconds = settingsQuery.data?.audioCueCaptureMaxOutroSeconds?.value ?? 60;

  // Decodes the whole episode in a background thread, so only runs on an
  // explicit scan; useScanQuery polls until the server reports the scan done
  // and exposes the same rescan idiom the other scan panels use.
  const { data, scanning: queryScanning, scanError, rescan } = useScanQuery({
    queryKey: ['cue-candidates', slug, episodeId],
    queryFn: () => getCueCandidates(slug, episodeId),
    rescanFn: () => getCueCandidates(slug, episodeId, true),
    enabled: scanned,
    savedErrorFallback: 'Scan failed.',
    thrownError: 'Scan failed. Try again.',
  });
  // Gate on `scanned`: a cached {status:'scanning'} envelope from a previous
  // mount would otherwise show a never-resolving spinner (the poll is disabled
  // until the user clicks Find) alongside the Find button.
  const scanning = scanned && queryScanning;

  const [showDismissed, setShowDismissed] = useState(false);
  const dismissalsQuery = useQuery({
    queryKey: ['cue-dismissals', slug],
    queryFn: () => listCueCandidateDismissals(slug),
    enabled: scanned,
  });
  const dismissals = dismissalsQuery.data ?? [];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['cue-candidates', slug, episodeId] });
    queryClient.invalidateQueries({ queryKey: ['cue-dismissals', slug] });
  };
  const dismissMutation = useMutation({
    mutationFn: (c: CueCandidate) => dismissCueCandidate(slug, episodeId, {
      start_s: c.start, end_s: c.end, label: cueCandidateLabel(c),
    }),
    onSuccess: invalidate,
  });
  const undoMutation = useMutation({
    mutationFn: (id: number) => undoCueCandidateDismissal(id),
    onSuccess: invalidate,
  });

  const candidates = (data?.candidates ?? []).filter((c) => !c.dismissed);
  const noneFound = data?.status === 'ready' && candidates.length === 0 && dismissals.length === 0;

  // Include kind: an intro and a recurring hit can share start/end on short
  // episodes, and a bare start-end key would collide (dup React keys + preview state).
  const candidateKey = (c: CueCandidate) => `${c.kind ?? 'recurring'}-${c.start}-${c.end}`;

  const stopPreview = () => {
    reqRef.current += 1;
    const a = audioRef.current;
    if (a) a.pause();
    if (previewStopRef.current) {
      previewStopRef.current();
      previewStopRef.current = null;
    }
    setPlayingKey(null);
  };

  const togglePreview = (c: CueCandidate) => {
    const a = audioRef.current;
    if (!a) return;
    if (playingKey === candidateKey(c)) {
      stopPreview();
      return;
    }
    // Cancel any in-flight preview, then play just this candidate's span and
    // stop at its end (same bounded-playback pattern as the capture modal).
    if (previewStopRef.current) {
      previewStopRef.current();
      previewStopRef.current = null;
    }
    reqRef.current += 1;
    const req = reqRef.current;
    const armStop = () => {
      const onTime = () => { if (a.currentTime >= c.end) stopPreview(); };
      a.addEventListener('timeupdate', onTime);
      previewStopRef.current = () => a.removeEventListener('timeupdate', onTime);
    };
    // If play is blocked (autoplay policy) or errors, reset so the button
    // doesn't stay stuck showing "pause" with no audio.
    if (a.readyState >= 1) {
      a.pause();
      a.currentTime = c.start;
      armStop();
      a.play().catch(() => stopPreview());
    } else {
      // Metadata not loaded yet. Call play() synchronously so it keeps the
      // click's user gesture (autoplay policy) and triggers the load; seek to
      // the candidate start once metadata arrives. Deferring play() into a
      // loadedmetadata callback loses the gesture and is blocked.
      a.play().then(() => {
        if (reqRef.current !== req) return;  // a newer click took over
        const seek = () => { a.currentTime = c.start; armStop(); };
        if (a.readyState >= 1) seek();
        else {
          const onMeta = () => { a.removeEventListener('loadedmetadata', onMeta); seek(); };
          a.addEventListener('loadedmetadata', onMeta);
          previewStopRef.current = () => a.removeEventListener('loadedmetadata', onMeta);
        }
      }).catch(() => stopPreview());
    }
    setPlayingKey(candidateKey(c));
  };

  // Stop playback if the component unmounts mid-preview.
  useEffect(() => () => {
    if (previewStopRef.current) previewStopRef.current();
  }, []);

  const makeTemplate = (c: CueCandidate) => {
    stopPreview();
    // Seed the capture type from the positional hint and pass the full span;
    // the modal clamps the region to the chosen type's ceiling, so switching
    // type there clamps against the right ceiling instead of a pre-truncated one.
    setSeed({ start: c.start, end: c.end, cueType: c.suggestedType ?? undefined });
  };

  return (
    <CollapsibleSection
      title="Audio Cues"
      subtitle="Find an audio cue to make a cue template"
      defaultOpen={false}
      storageKey={`episode-cue-candidates-${episodeId}`}
    >
      <p className="text-sm text-muted-foreground mb-3">
        Scan for audio cues: ad-break stings that repeat within the episode, plus
        intros and outros shared with other episodes of this feed.
      </p>

      {!scanned && (
        <button
          onClick={() => setScanned(true)}
          disabled={!hasOriginalAudio}
          title={hasOriginalAudio ? '' : 'Original audio not retained for this episode'}
          className={makeBtn}
        >
          Find audio cues
        </button>
      )}

      {scanning && (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <LoadingSpinner size="sm" inline className="w-4 h-4" /> Scanning audio, this can take a minute on a long episode...
        </p>
      )}
      {!scanning && scanError && (
        <div className="flex items-center gap-3">
          <p className="text-sm text-destructive">{scanError}</p>
          <button onClick={() => rescan()} className={makeBtn}>Try again</button>
        </div>
      )}
      {noneFound && (
        <p className="text-sm text-muted-foreground">No audio cues found.</p>
      )}

      {(candidates.length > 0 || dismissals.length > 0) && (
        <div className="space-y-2">
          {candidates.map((c) => {
            const key = candidateKey(c);
            const isPlaying = playingKey === key;
            return (
              <div key={key} className="p-3 bg-secondary/40 rounded-lg border border-border">
                <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <button
                      onClick={() => togglePreview(c)}
                      disabled={!hasOriginalAudio}
                      aria-label={isPlaying ? 'Stop preview' : 'Play candidate'}
                      title={hasOriginalAudio
                        ? (isPlaying ? 'Stop' : 'Play this sound')
                        : 'Original audio not retained for this episode'}
                      className="shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-full border border-border bg-background text-foreground hover:bg-accent disabled:opacity-50 transition-colors touch-manipulation"
                    >
                      {isPlaying ? (
                        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                          <rect x="6" y="5" width="4" height="14" rx="1" />
                          <rect x="14" y="5" width="4" height="14" rx="1" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4 ml-0.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                          <path d="M8 5v14l11-7z" />
                        </svg>
                      )}
                    </button>
                    <div className="min-w-0 flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-foreground whitespace-nowrap">
                        {formatTimestamp(c.start)} - {formatTimestamp(c.end)}
                      </span>
                      <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                        c.kind === 'intro' || c.kind === 'outro'
                          ? 'bg-amber-500/20 text-warning'
                          : 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
                      }`}>
                        {cueCandidateLabel(c)}
                      </span>
                      {c.kind === 'recurring' && c.suggestedType && (
                        <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground">
                          {c.suggestedType.replace(/_/g, ' ')}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2 sm:shrink-0">
                    <button
                      onClick={() => makeTemplate(c)}
                      disabled={!hasOriginalAudio}
                      title={hasOriginalAudio ? 'Open the capture tool to make a template'
                        : 'Original audio not retained for this episode'}
                      className={`${makeBtn} flex-1 sm:flex-none`}
                    >
                      Make template
                    </button>
                    <button
                      onClick={() => { stopPreview(); dismissMutation.mutate(c); }}
                      disabled={dismissMutation.isPending}
                      title="Not a cue: hide this sound in every episode of this feed"
                      className={`${secondaryBtn} flex-1 sm:flex-none`}
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
          <div className="pt-1">
            <button
              onClick={() => rescan()}
              className={secondaryBtn}
            >
              Rescan
            </button>
          </div>
          {dismissals.length > 0 && (
            <div className="pt-1">
              <button
                onClick={() => setShowDismissed((v) => !v)}
                className="text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                {showDismissed ? 'Hide dismissed' : `Dismissed (${dismissals.length})`}
              </button>
              {showDismissed && (
                <div className="mt-2 space-y-2">
                  {dismissals.map((d) => (
                    <div key={d.id} className="p-2 bg-muted/40 rounded border border-border flex items-center gap-3">
                      <span className="font-mono text-xs text-muted-foreground">
                        {formatTimestamp(d.startS)} - {formatTimestamp(d.endS)}
                      </span>
                      <span className="flex-1 min-w-0 text-xs text-muted-foreground truncate">
                        {d.label || 'Dismissed sound'}
                        {d.sourceEpisodeId !== episodeId && ' (from another episode)'}
                      </span>
                      <button
                        onClick={() => undoMutation.mutate(d.id)}
                        disabled={undoMutation.isPending}
                        className={`shrink-0 px-2 py-1 text-xs rounded font-medium ${btnSecondary} disabled:opacity-50`}
                      >
                        Undo
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <audio
            ref={audioRef}
            src={episodeOriginalUrl(slug, episodeId)}
            preload="metadata"
            className="hidden"
          />
        </div>
      )}

      {seed && (
        <CueMarkModal
          podcastSlug={slug}
          episodeId={episodeId}
          episodeTitle={episodeTitle}
          episodeDuration={episodeDuration}
          initialStart={seed.start}
          initialEnd={seed.end}
          initialCueType={seed.cueType}
          captureMinSeconds={captureMinSeconds}
          captureMaxSeconds={captureMaxSeconds}
          captureMaxIntroSeconds={captureMaxIntroSeconds}
          captureMaxOutroSeconds={captureMaxOutroSeconds}
          onClose={() => setSeed(null)}
          onSaved={() => queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] })}
          onFinalSave={() => setSeed(null)}
        />
      )}
    </CollapsibleSection>
  );
}

export default CueCandidatesSection;
