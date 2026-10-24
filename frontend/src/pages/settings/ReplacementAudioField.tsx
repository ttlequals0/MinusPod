import { useEffect, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../../api/client';
import {
  getReplacementAudio,
  revertReplacementAudio,
  uploadReplacementAudio,
} from '../../api/settings';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { badgeBase } from '../../components/badgeStyles';
import { iconTarget, iconVisual } from '../../components/rowActionStyles';

// The splice bar reads as a beep sitting inside content, so its width has to
// mean something. Scale duration against this, since typical markers are 1-3s.
const BAR_REFERENCE_SECONDS = 5;
const MIN_BLOCK_FRACTION = 0.08;
const MAX_BLOCK_FRACTION = 0.7;

function blockFraction(seconds: number | null): number {
  if (!seconds) return MIN_BLOCK_FRACTION;
  const raw = seconds / BAR_REFERENCE_SECONDS;
  return Math.min(MAX_BLOCK_FRACTION, Math.max(MIN_BLOCK_FRACTION, raw));
}

function describeFile(
  duration: number | null, channels: number | null, sampleRate: number | null,
): string {
  const parts: string[] = [];
  if (duration != null) parts.push(`${duration.toFixed(2)}s`);
  if (channels != null) parts.push(channels === 1 ? 'mono' : channels === 2 ? 'stereo' : `${channels} ch`);
  if (sampleRate != null) parts.push(`${(sampleRate / 1000).toFixed(1)} kHz`);
  return parts.join(' · ');
}

function ReplacementAudioField() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['replacementAudio'],
    queryFn: getReplacementAudio,
  });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // updatedAt busts the browser cache: the URL is stable across uploads.
  const src = `/api/v1/settings/replacement-audio/file?v=${data?.updatedAt ?? 0}`;

  useEffect(() => {
    // A swapped file must not keep playing the old one.
    const el = audioRef.current;
    if (!el) return;
    el.pause();
    el.load();
    setPlaying(false);
    setProgress(0);
  }, [data?.updatedAt]);

  const settle = () => {
    setError(null);
    qc.invalidateQueries({ queryKey: ['replacementAudio'] });
  };

  const upload = useMutation({
    mutationFn: (file: File) => uploadReplacementAudio(file),
    onSuccess: settle,
    onError: (e: unknown) => setError(getErrorMessage(e, 'Could not upload audio.')),
  });

  const revert = useMutation({
    mutationFn: revertReplacementAudio,
    onSuccess: settle,
    onError: (e: unknown) => setError(getErrorMessage(e, 'Could not restore the default')),
  });

  const busy = upload.isPending || revert.isPending;

  const togglePlay = () => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
      return;
    }
    el.currentTime = 0;
    el.play().catch(() => setError('That file could not be played in this browser.'));
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (file) upload.mutate(file);
  };

  const duration = data?.durationSeconds ?? null;
  const isCustom = data?.source === 'uploaded';
  const fraction = blockFraction(duration);

  return (
    <div className="pt-4 border-t border-border">
      <div className="flex items-center justify-between gap-3 mb-2">
        <span className="block text-sm font-medium text-foreground">Replacement audio</span>
        <span className={`${badgeBase} border border-border text-muted-foreground shrink-0`}>
          {isCustom ? 'Your file' : 'Default'}
        </span>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac"
        className="hidden"
        onChange={onPick}
      />
      <audio
        ref={audioRef}
        src={src}
        preload="none"
        className="hidden"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => { setPlaying(false); setProgress(0); }}
        onTimeUpdate={(e) => {
          const el = e.currentTarget;
          if (el.duration) setProgress(el.currentTime / el.duration);
        }}
      />

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={togglePlay}
          disabled={!data?.exists || busy}
          aria-label={playing ? 'Stop the replacement audio' : 'Play the replacement audio'}
          className={iconTarget}
        >
          <span className={`${iconVisual} ${btnSecondary}`}>
            {playing ? (
              <Pause className="w-4 h-4" fill="currentColor" aria-hidden="true" />
            ) : (
              <Play className="w-4 h-4" fill="currentColor" aria-hidden="true" />
            )}
          </span>
        </button>

        {/* Content, replacement, content. Width of the middle block tracks
            duration, so a long file visibly takes over more of the episode. */}
        <div
          aria-hidden="true"
          className="flex-1 h-6 rounded flex items-stretch overflow-hidden bg-muted/40 border border-border"
        >
          <div className="flex-1 opacity-40 bg-[repeating-linear-gradient(90deg,currentColor_0_1px,transparent_1px_5px)] text-muted-foreground" />
          <div
            className="relative bg-primary/25 border-x border-primary/60"
            style={{ width: `${fraction * 100}%` }}
          >
            <div
              className="absolute inset-y-0 left-0 bg-primary/70"
              style={{ width: playing ? `${progress * 100}%` : '0%' }}
            />
          </div>
          <div className="flex-1 opacity-40 bg-[repeating-linear-gradient(90deg,currentColor_0_1px,transparent_1px_5px)] text-muted-foreground" />
        </div>

        <span className="text-sm text-muted-foreground shrink-0 tabular-nums">
          {isLoading ? 'Loading' : !data?.exists ? 'Missing'
            : describeFile(duration, data.channels, data.sampleRateHz)}
        </span>
      </div>

      <p className="mt-2 text-sm text-muted-foreground">
        Used for replacement sound and beep actions.
      </p>

      {isCustom && data?.channels === 1 && (
        <p className="mt-2 pl-3 border-l-2 border-border text-sm text-muted-foreground">
          This file is mono. An episode whose first cut starts at 0:00 will come out mono.
          A stereo file avoids that.
        </p>
      )}

      {!data?.exists && !isLoading && (
        <p className="mt-2 text-sm text-destructive">
          No replacement audio is installed, so cut ads render as silence. Upload a file to fix it.
        </p>
      )}

      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
          className={`${btnBase} ${btnSecondary}`}
        >
          {upload.isPending ? 'Uploading...' : 'Upload a file'}
        </button>
        {data?.canRevert && (
          <button
            type="button"
            onClick={() => revert.mutate()}
            disabled={busy}
            className={`${btnBase} ${btnSecondary}`}
          >
            {revert.isPending ? 'Restoring...' : 'Use the default'}
          </button>
        )}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        MP3, WAV, M4A, OGG or FLAC. Up to 5 MB and 30 seconds. Converted to MP3 on upload.
      </p>
    </div>
  );
}

export default ReplacementAudioField;
