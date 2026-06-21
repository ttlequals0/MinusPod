import { useState, useRef, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from './CollapsibleSection';
import LoadingSpinner from './LoadingSpinner';
import CueMarkModal from './CueMarkModal';
import { getCueCandidates, type CueCandidate } from '../api/cueTemplates';
import { getSettings } from '../api/settings';

interface CueCandidatesSectionProps {
  slug: string;
  episodeId: string;
  episodeTitle: string;
  episodeDuration: number;
  hasOriginalAudio: boolean;
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

const makeBtn =
  'px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0';

function CueCandidatesSection({
  slug, episodeId, episodeTitle, episodeDuration, hasOriginalAudio,
}: CueCandidatesSectionProps) {
  const queryClient = useQueryClient();
  const [scanned, setScanned] = useState(false);
  const [seed, setSeed] = useState<{ start: number; end: number } | null>(null);

  // Inline preview: one shared <audio> plays just the candidate's [start, end]
  // span so a candidate can be heard without opening the capture modal.
  const audioRef = useRef<HTMLAudioElement>(null);
  const previewStopRef = useRef<(() => void) | null>(null);
  const [playingKey, setPlayingKey] = useState<string | null>(null);

  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const captureMinSeconds = settingsQuery.data?.audioCueCaptureMinSeconds?.value ?? 0.2;
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 10;
  const captureMaxIntroSeconds = settingsQuery.data?.audioCueCaptureMaxIntroSeconds?.value ?? 60;
  const captureMaxOutroSeconds = settingsQuery.data?.audioCueCaptureMaxOutroSeconds?.value ?? 60;

  // Decodes the whole episode in a background thread, so only runs on an
  // explicit scan and polls until the server reports the scan is done.
  const candidatesQuery = useQuery({
    queryKey: ['cue-candidates', slug, episodeId],
    queryFn: () => getCueCandidates(slug, episodeId),
    enabled: scanned,
    staleTime: Infinity,
    refetchInterval: (query) =>
      query.state.data?.status === 'scanning' ? 3000 : false,
  });

  const data = candidatesQuery.data;
  const candidates: CueCandidate[] = data?.candidates ?? [];
  const scanning = scanned && (candidatesQuery.isLoading || data?.status === 'scanning');
  const scanError = data?.status === 'error'
    ? (data.error || 'Scan failed.')
    : (candidatesQuery.error ? 'Scan failed. Try again.' : null);
  const noneFound = data?.status === 'ready' && candidates.length === 0;

  const rescan = () =>
    queryClient.fetchQuery({
      queryKey: ['cue-candidates', slug, episodeId],
      queryFn: () => getCueCandidates(slug, episodeId, true),
      staleTime: 0,
    });

  const candidateKey = (c: CueCandidate) => `${c.start}-${c.end}`;

  const stopPreview = () => {
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
    a.pause();
    const begin = () => {
      a.currentTime = c.start;
      const onTime = () => { if (a.currentTime >= c.end) stopPreview(); };
      a.addEventListener('timeupdate', onTime);
      previewStopRef.current = () => a.removeEventListener('timeupdate', onTime);
      // If play is blocked (autoplay policy) or errors, reset so the button
      // doesn't stay stuck showing "pause" with no audio.
      a.play().catch(() => stopPreview());
    };
    if (a.readyState >= 1) {
      begin();
    } else {
      const onMeta = () => { a.removeEventListener('loadedmetadata', onMeta); begin(); };
      a.addEventListener('loadedmetadata', onMeta);
      previewStopRef.current = () => a.removeEventListener('loadedmetadata', onMeta);
      a.load();
    }
    setPlayingKey(candidateKey(c));
  };

  // Stop playback if the component unmounts mid-preview.
  useEffect(() => () => {
    if (previewStopRef.current) previewStopRef.current();
  }, []);

  const makeTemplate = (start: number, end: number) => {
    stopPreview();
    setSeed({ start, end: Math.min(end, start + captureMaxSeconds) });
  };

  return (
    <CollapsibleSection
      title="Cue Candidates"
      subtitle="Find a recurring sound to make a cue template"
      defaultOpen={false}
      storageKey={`episode-cue-candidates-${episodeId}`}
    >
      <p className="text-sm text-muted-foreground mb-3">
        Scan the audio for sounds that repeat across the episode -- the kind worth
        templating. One-off loud moments are skipped.
      </p>

      {!scanned && (
        <button
          onClick={() => setScanned(true)}
          disabled={!hasOriginalAudio}
          title={hasOriginalAudio ? '' : 'Original audio not retained for this episode'}
          className={makeBtn}
        >
          Find cue candidates
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
        <p className="text-sm text-muted-foreground">No recurring sounds found.</p>
      )}

      {candidates.length > 0 && (
        <div className="space-y-2">
          {candidates.map((c) => {
            const key = candidateKey(c);
            const isPlaying = playingKey === key;
            return (
              <div key={key} className="p-3 bg-secondary/40 rounded-lg border border-border">
                <div className="flex items-center gap-3">
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
                  <div className="flex-1 min-w-0 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-foreground">
                      {formatTime(c.start)} - {formatTime(c.end)}
                    </span>
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                      Repeats {c.count}x
                    </span>
                    {c.prominenceDb != null && (
                      <span className="text-xs text-muted-foreground">{c.prominenceDb.toFixed(1)} dB</span>
                    )}
                  </div>
                  <button
                    onClick={() => makeTemplate(c.start, c.end)}
                    disabled={!hasOriginalAudio}
                    title={hasOriginalAudio ? 'Open the capture tool to make a template'
                      : 'Original audio not retained for this episode'}
                    className={`${makeBtn} shrink-0`}
                  >
                    Make template
                  </button>
                </div>
              </div>
            );
          })}
          <audio
            ref={audioRef}
            src={`/api/v1/feeds/${slug}/episodes/${episodeId}/original.mp3`}
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
