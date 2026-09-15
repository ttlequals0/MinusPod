import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import {
  episodeOriginalUrl,
  getOriginalSegments,
} from '../api/feeds';
import type { AdSegment, EpisodeDetail as EpisodeDetailType } from '../api/types';
import type { AdCorrection } from '../components/AdEditor';
import { useEpisodeRecutWatch } from '../hooks/useEpisodeRecutWatch';
import { btnDestructive, btnPrimary, btnSecondary } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { formatTime } from '../utils/adReviewHelpers';
import {
  allEpisodeMarkers,
  boundsFromManualTimes,
  boundsFromSelectedIndexes,
  buildSegmentReviewRows,
  ensureMinTextTemplate,
  findOverlappingMarker,
  labelDisplay,
  labelPillClass,
  MIN_TEXT_TEMPLATE_CHARS,
  rangeIndexes,
  rowBackgroundClass,
  selectionOverlapsKept,
  type AppliedCutRange,
  type SegmentReviewRow,
} from '../utils/segmentReviewModel';

type SaveStatus = 'idle' | 'saving' | 'success' | 'error';

interface Props {
  slug: string;
  episodeId: string;
  episode: EpisodeDetailType;
  onClose: () => void;
  onSubmitCorrection: (correction: AdCorrection) => Promise<void>;
  onRecut: () => Promise<void>;
  onOpenWaveform: (opts: { start: number; end: number; createMode: boolean; marker?: AdSegment }) => void;
  saveStatus?: SaveStatus;
  correctionError?: string | null;
}

function TranscriptSegmentWorkspace({
  slug,
  episodeId,
  episode,
  onClose,
  onSubmitCorrection,
  onRecut,
  onOpenWaveform,
  saveStatus = 'idle',
  correctionError = null,
}: Props) {
  const queryClient = useQueryClient();
  const originalAudioRef = useRef<HTMLAudioElement>(null);
  const [selectedIndexes, setSelectedIndexes] = useState<Set<number>>(() => new Set());
  const [rangeAnchor, setRangeAnchor] = useState<number | null>(null);
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [currentTime, setCurrentTime] = useState(0);
  const [sponsor, setSponsor] = useState('');
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const recutWatch = useEpisodeRecutWatch(slug, episodeId, episode.title);

  const { data: segmentsData, isLoading, error: segmentsError } = useQuery({
    queryKey: ['originalSegments', slug, episodeId],
    queryFn: () => getOriginalSegments(slug, episodeId),
    enabled: !!slug && !!episodeId,
  });

  const segments = segmentsData?.segments ?? [];
  const appliedCuts: AppliedCutRange[] = useMemo(
    () => (episode.appliedCuts ?? []).map((c) => ({
      start: c.start,
      end: c.end,
      replacementDuration: c.replacementDuration,
    })),
    [episode.appliedCuts],
  );

  const rows = useMemo(
    () => buildSegmentReviewRows(segments, episode, appliedCuts),
    [segments, episode, appliedCuts],
  );

  const allMarkers = useMemo(() => allEpisodeMarkers(episode), [episode]);

  const selectionSummary = useMemo(() => {
    if (selectedIndexes.size === 0) return null;
    const groups = boundsFromSelectedIndexes(selectedIndexes, rows, segments);
    return `${groups.length} span${groups.length === 1 ? '' : 's'} · ${selectedIndexes.size} segment${selectedIndexes.size === 1 ? '' : 's'} selected`;
  }, [selectedIndexes, rows, segments]);

  const playFrom = useCallback((time: number) => {
    const audio = originalAudioRef.current;
    if (!audio) return;
    audio.currentTime = time;
    void audio.play();
  }, []);

  const updateBoundsFromSelection = useCallback(
    (indexes: Set<number>) => {
      const groups = boundsFromSelectedIndexes(indexes, rows, segments);
      if (groups.length !== 1) return;
      const bounds = groups[0];
      setStartTime(bounds.start.toFixed(1));
      setEndTime(bounds.end.toFixed(1));
    },
    [rows, segments],
  );

  const toggleSegmentSelection = useCallback(
    (index: number, shiftKey: boolean) => {
      setSelectedIndexes((prev) => {
        let next: Set<number>;
        if (shiftKey && rangeAnchor !== null) {
          next = new Set([...prev, ...rangeIndexes(rangeAnchor, index)]);
        } else {
          next = new Set(prev);
          if (next.has(index)) next.delete(index);
          else next.add(index);
        }
        updateBoundsFromSelection(next);
        return next;
      });
      setRangeAnchor(index);
    },
    [rangeAnchor, updateBoundsFromSelection],
  );

  const clearSelection = useCallback(() => {
    setSelectedIndexes(new Set());
    setRangeAnchor(null);
    setStartTime('');
    setEndTime('');
  }, []);

  const resolveSelectionBounds = useCallback((): { start: number; end: number; text: string }[] => {
    if (selectedIndexes.size > 0) {
      return boundsFromSelectedIndexes(selectedIndexes, rows, segments);
    }
    const manual = boundsFromManualTimes(Number(startTime), Number(endTime), segments);
    return manual ? [manual] : [];
  }, [selectedIndexes, rows, segments, startTime, endTime]);

  const refreshAfterCorrection = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    await queryClient.invalidateQueries({ queryKey: ['originalSegments', slug, episodeId] });
  }, [queryClient, slug, episodeId]);

  useEffect(() => {
    if (saveStatus === 'success') {
      void refreshAfterCorrection();
      clearSelection();
      setStatusMessage('Correction saved.');
    }
  }, [saveStatus, refreshAfterCorrection, clearSelection]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const markContent = async () => {
    setLocalError(null);
    const boundsList = resolveSelectionBounds();
    if (boundsList.length === 0) {
      setLocalError('Select segment rows or enter start and end times.');
      return;
    }
    if (boundsList.some((b) => selectionOverlapsKept(b, episode.keptMarkers))) {
      setLocalError('Selection overlaps a kept segment; kept markers cannot be corrected.');
      return;
    }
    try {
      for (const bounds of boundsList) {
        const marker = findOverlappingMarker(bounds.start, bounds.end, allMarkers);
        await onSubmitCorrection({
          type: 'reject',
          originalAd: {
            start: bounds.start,
            end: bounds.end,
            pattern_id: marker?.pattern_id,
            confidence: marker?.confidence ?? 1,
            reason: marker?.reason || '',
            sponsor: marker?.sponsor,
          },
        });
      }
      setStatusMessage(
        `Saved ${boundsList.length} content correction${boundsList.length === 1 ? '' : 's'}.`,
      );
    } catch {
      setLocalError('Failed to save correction.');
    }
  };

  const markAd = async () => {
    setLocalError(null);
    const sponsorText = sponsor.trim();
    if (!sponsorText) {
      setLocalError('Enter a sponsor name to mark a missed ad.');
      return;
    }
    const boundsList = resolveSelectionBounds();
    if (boundsList.length === 0) {
      setLocalError('Select segment rows or enter start and end times.');
      return;
    }
    const templates = boundsList.map((bounds) =>
      ensureMinTextTemplate(bounds.text, segments, bounds.start, bounds.end),
    );
    const shortIdx = templates.findIndex((t) => t.length < MIN_TEXT_TEMPLATE_CHARS);
    if (shortIdx >= 0) {
      setLocalError(
        `Selected text is too short (${templates[shortIdx].length} chars). Select more segments.`,
      );
      return;
    }
    try {
      for (let i = 0; i < boundsList.length; i += 1) {
        const bounds = boundsList[i];
        await onSubmitCorrection({
          type: 'create',
          start: bounds.start,
          end: bounds.end,
          sponsor: sponsorText,
          text_template: templates[i],
          scope: 'podcast',
          reason: `${sponsorText}: manually marked from transcript`,
        });
      }
      setStatusMessage(
        `Saved ${boundsList.length} missed ad${boundsList.length === 1 ? '' : 's'}.`,
      );
    } catch {
      setLocalError('Failed to save correction.');
    }
  };

  const handleRecut = async () => {
    setLocalError(null);
    recutWatch.dismissCompletion();
    recutWatch.startWatching();
    try {
      await onRecut();
      setStatusMessage('Recut queued — progress updates below.');
    } catch {
      recutWatch.stopWatching();
      setLocalError('Failed to start recut.');
      setStatusMessage(null);
    }
  };

  const handleFineTune = () => {
    const boundsList = resolveSelectionBounds();
    if (boundsList.length !== 1) {
      setLocalError('Select one span to open in the waveform editor.');
      return;
    }
    const bounds = boundsList[0];
    const marker = findOverlappingMarker(bounds.start, bounds.end, allMarkers);
    onOpenWaveform({
      start: bounds.start,
      end: bounds.end,
      createMode: !marker,
      marker: marker ?? undefined,
    });
  };

  const busy = saveStatus === 'saving' || recutWatch.watching;
  const displayError = localError || correctionError;
  const recutActive = recutWatch.phase === 'queued' || recutWatch.phase === 'processing';
  const originalAudioUrl = episode.hasOriginalAudio
    ? episodeOriginalUrl(slug, episodeId)
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-background"
      role="dialog"
      aria-modal="true"
      aria-label="Transcript segment review"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground truncate">
            Review transcript
          </h2>
          <p className="text-sm text-muted-foreground truncate">{episode.title}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close transcript review"
          className={`shrink-0 rounded-md p-2 ${btnSecondary} ${focusRing}`}
        >
          <X className="h-5 w-5" />
        </button>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="shrink-0 space-y-3 border-b border-border px-4 py-4 sm:px-6">
          {originalAudioUrl ? (
            <div>
              <p className="mb-2 text-sm text-muted-foreground">
                Original audio. Click a row to play. Check rows to mark them; Shift+click a checkbox for a range.
              </p>
              <audio
                ref={originalAudioRef}
                controls
                className="w-full"
                src={originalAudioUrl}
                preload="metadata"
                onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              />
            </div>
          ) : (
            <p className="text-sm text-amber-700 dark:text-amber-300">
              Original audio is not retained for this episode; marking still updates patterns for future runs.
            </p>
          )}

          <div className="flex flex-wrap items-end gap-2">
            <label className="text-sm text-muted-foreground">
              Start
              <input
                type="text"
                value={startTime}
                onChange={(e) => setStartTime(e.target.value)}
                className="mt-1 block w-24 rounded-md border border-input bg-background px-2 py-1 text-sm"
              />
            </label>
            <label className="text-sm text-muted-foreground">
              End
              <input
                type="text"
                value={endTime}
                onChange={(e) => setEndTime(e.target.value)}
                className="mt-1 block w-24 rounded-md border border-input bg-background px-2 py-1 text-sm"
              />
            </label>
            <label className="text-sm text-muted-foreground flex-1 min-w-[10rem]">
              Sponsor (for Mark ad)
              <input
                type="text"
                value={sponsor}
                onChange={(e) => setSponsor(e.target.value)}
                placeholder="e.g. Squarespace"
                className="mt-1 block w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
              />
            </label>
          </div>

          {selectionSummary && (
            <p className="text-sm text-muted-foreground">{selectionSummary}</p>
          )}

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={markContent}
              className={`px-3 py-1.5 text-sm rounded-md ${btnSecondary} disabled:opacity-50 ${focusRing}`}
            >
              Mark content
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={markAd}
              className={`px-3 py-1.5 text-sm rounded-md ${btnDestructive} disabled:opacity-50 ${focusRing}`}
            >
              Mark ad
            </button>
            <button
              type="button"
              disabled={busy || !episode.hasOriginalAudio}
              onClick={() => { void handleRecut(); }}
              className={`px-3 py-1.5 text-sm rounded-md ${btnPrimary} disabled:opacity-50 ${focusRing}`}
            >
              {recutWatch.watching ? 'Recutting…' : 'Recut audio'}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={handleFineTune}
              className={`px-3 py-1.5 text-sm rounded-md ${btnSecondary} disabled:opacity-50 ${focusRing}`}
            >
              Fine-tune in waveform
            </button>
            {selectedIndexes.size > 0 && (
              <button
                type="button"
                onClick={clearSelection}
                className={`px-3 py-1.5 text-sm rounded-md ${btnSecondary} ${focusRing}`}
              >
                Clear selection
              </button>
            )}
          </div>

          {recutActive && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="font-medium text-foreground">
                  {recutWatch.stageLabel ?? 'Recutting'}
                </span>
                <span className="tabular-nums text-muted-foreground">
                  {Math.round(recutWatch.progress)}%
                </span>
              </div>
              <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full bg-primary transition-all duration-300"
                  style={{ width: `${Math.max(recutWatch.progress, recutWatch.phase === 'queued' ? 8 : 3)}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {recutWatch.phase === 'queued'
                  ? 'Waiting in the processing queue…'
                  : 'Updating processed audio from your corrections…'}
              </p>
            </div>
          )}

          {recutWatch.phase === 'completed' && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-emerald-800 dark:text-emerald-200">
                  Recut complete
                </p>
                <p className="text-sm text-emerald-700 dark:text-emerald-300">
                  Processed audio is updated. Segment labels will refresh on the next load.
                </p>
              </div>
              <button
                type="button"
                onClick={recutWatch.dismissCompletion}
                className={`shrink-0 text-sm ${btnSecondary} px-2 py-1 rounded ${focusRing}`}
              >
                Dismiss
              </button>
            </div>
          )}

          {recutWatch.phase === 'failed' && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-destructive">Recut failed</p>
                <p className="text-sm text-destructive/90">
                  Check the episode status or processing logs for details.
                </p>
              </div>
              <button
                type="button"
                onClick={recutWatch.dismissCompletion}
                className={`shrink-0 text-sm ${btnSecondary} px-2 py-1 rounded ${focusRing}`}
              >
                Dismiss
              </button>
            </div>
          )}

          {displayError && (
            <p className="text-sm text-destructive">{displayError}</p>
          )}
          {statusMessage && !displayError && (
            <p className="text-sm text-muted-foreground">{statusMessage}</p>
          )}
          {saveStatus === 'saving' && (
            <p className="text-sm text-muted-foreground">Saving…</p>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 pb-6 sm:px-6">
          {isLoading && (
            <p className="py-6 text-sm text-muted-foreground">Loading segments…</p>
          )}
          {segmentsError && (
            <p className="py-6 text-sm text-destructive">Failed to load transcript segments.</p>
          )}
          {!isLoading && !segmentsError && rows.length === 0 && (
            <p className="py-6 text-sm text-muted-foreground">No transcript segments available.</p>
          )}
          {rows.length > 0 && (
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-background border-b border-border">
                <tr>
                  <th className="w-10 py-2 pr-2 font-medium text-muted-foreground"> </th>
                  <th className="w-12 py-2 pr-2 font-medium text-muted-foreground">#</th>
                  <th className="w-36 py-2 pr-2 font-medium text-muted-foreground">Time</th>
                  <th className="w-28 py-2 pr-2 font-medium text-muted-foreground">Label</th>
                  <th className="py-2 font-medium text-muted-foreground">Text</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <SegmentTableRow
                    key={row.index}
                    row={row}
                    selected={selectedIndexes.has(row.index)}
                    playing={currentTime >= row.start && currentTime < row.end}
                    onToggleSelect={(shiftKey) => toggleSegmentSelection(row.index, shiftKey)}
                    onRowClick={() => playFrom(row.start)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function SegmentTableRow({
  row,
  selected,
  playing,
  onToggleSelect,
  onRowClick,
}: {
  row: SegmentReviewRow;
  selected: boolean;
  playing: boolean;
  onToggleSelect: (shiftKey: boolean) => void;
  onRowClick: () => void;
}) {
  return (
    <tr
      className={`border-b border-border/60 cursor-pointer ${rowBackgroundClass(row, { selected, playing })}`}
      onClick={onRowClick}
    >
      <td className="py-2 pr-2 align-top" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          aria-label={`Select segment ${row.sequenceNum}`}
          onChange={(e) => {
            e.stopPropagation();
            onToggleSelect((e.nativeEvent as MouseEvent).shiftKey);
          }}
        />
      </td>
      <td className="py-2 pr-2 align-top tabular-nums text-muted-foreground">{row.sequenceNum}</td>
      <td className="py-2 pr-2 align-top tabular-nums whitespace-nowrap text-muted-foreground">
        {formatTime(row.start)} – {formatTime(row.end)}
      </td>
      <td className="py-2 pr-2 align-top">
        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${labelPillClass(row.label)}`}>
          {labelDisplay(row.label, row.mixed)}
        </span>
      </td>
      <td className="py-2 align-top text-foreground">{row.text}</td>
    </tr>
  );
}

export default TranscriptSegmentWorkspace;
