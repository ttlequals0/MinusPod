import { useState } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Play, Square, X } from 'lucide-react';
import LoadingSpinner from './LoadingSpinner';
import CueMarkModal from './CueMarkModal';
import { ghostBtn, primaryBtn, modalBackdrop, modalPanel, useEscape } from './cueScanStyles';
import { useScanQuery } from '../hooks/useScanQuery';
import {
  crossEpisodeScan,
  type CrossEpisodeCandidate,
  type CrossEpisodeScanResponse,
} from '../api/cueTemplates';
import { episodeOriginalUrl, getEpisode, getEpisodes } from '../api/feeds';
import type { Episode } from '../api/types';
import { formatDate, formatTimestamp } from '../utils/format';
import { useAuditionPlayer } from '../hooks/useAuditionPlayer';

const PICKER_PAGE_SIZE = 50;
// Maximum episodes a user may select for the cross-episode scan (server cap).
const CROSS_EPISODE_MAX = 5;
const CROSS_EPISODE_MIN = 2;

interface CueCrossEpisodeScanModalProps {
  slug: string;
  captureMinSeconds: number;
  captureMaxSeconds: number;
  captureMaxIntroSeconds: number;
  captureMaxOutroSeconds: number;
  onClose: () => void;
  onSaved: () => void;
}

export default function CueCrossEpisodeScanModal({
  slug,
  captureMinSeconds,
  captureMaxSeconds,
  captureMaxIntroSeconds,
  captureMaxOutroSeconds,
  onClose,
  onSaved,
}: CueCrossEpisodeScanModalProps) {
  const [pickerPage, setPickerPage] = useState(0);
  // Selected episodes in click order (first = target). Full objects, not ids,
  // so title/duration survive paging away from the page they were picked on.
  const [selected, setSelected] = useState<Episode[]>([]);
  // Phase: picker -> results (scanning/ready/error handled in scanQuery state).
  const [phase, setPhase] = useState<'picker' | 'results'>('picker');
  // Seed for CueMarkModal when a candidate's "Make template" is clicked.
  const [seed, setSeed] = useState<CrossEpisodeCandidate | null>(null);
  // Per-candidate breakdown expansion (one at a time), keyed `${start}-${end}`.
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  // Shared windowed playback for candidate rows and per-match chips. No
  // preloadSrc: rows play different episodes' originals.
  const { playingKey, toggle: togglePlayback, stop: stopPlayback, audioElement } =
    useAuditionPlayer();

  // Escape closes this modal, but only when no stacked CueMarkModal is open --
  // the seed modal owns Escape while it is up, so the parent must stand down.
  useEscape(seed ? () => {} : onClose);

  const pickerQuery = useQuery({
    queryKey: ['cue-template-picker', slug, pickerPage],
    queryFn: () =>
      getEpisodes(slug, {
        limit: PICKER_PAGE_SIZE,
        offset: pickerPage * PICKER_PAGE_SIZE,
        status: 'completed',
        sortBy: 'published',
        sortDir: 'desc',
      }),
    enabled: !!slug,
  });

  const pickerEpisodes = (pickerQuery.data?.episodes ?? [])
    .filter((ep) => ep.hasOriginalAudio !== false);
  const pickerTotal = pickerQuery.data?.total ?? 0;
  const pickerTotalPages = Math.max(1, Math.ceil(pickerTotal / PICKER_PAGE_SIZE));

  const selectedIds = selected.map((ep) => ep.id);

  // React Query scan: enabled once user advances to results phase.
  // Polling stops when status is no longer 'scanning'.
  const scanQueryKey = ['cue-cross-episode-scan', slug, selectedIds];
  const { data: scanData, scanning, scanError, rescan } =
    useScanQuery<CrossEpisodeScanResponse>({
      queryKey: scanQueryKey,
      queryFn: () => crossEpisodeScan(slug, selectedIds),
      rescanFn: () => crossEpisodeScan(slug, selectedIds, true),
      enabled: phase === 'results' && selected.length >= CROSS_EPISODE_MIN,
      savedErrorFallback: 'Scan failed.',
      thrownError: 'Scan failed. Try again.',
    });

  const candidates: CrossEpisodeCandidate[] = scanData?.candidates ?? [];
  // Total episodes the badge denominator refers to (M); prefer the response's
  // set, fall back to the current selection while no response has arrived.
  const episodeCount = scanData?.episodeIds?.length ?? selectedIds.length;

  // Candidate times live in the response's targetEpisodeId frame. The server
  // cache is keyed on the SORTED id set, so a cached scan queued in a different
  // order returns candidates in a DIFFERENT episode's timeline than selected[0].
  // Resolve the display/seed episode from the response; fall back to selected[0]
  // only until a response arrives.
  const targetEpId = scanData?.targetEpisodeId ?? selected[0]?.id;
  const knownTargetEp =
    selected.find((ep) => ep.id === targetEpId) ??
    pickerEpisodes.find((ep) => ep.id === targetEpId);

  // If the response names an episode we do not hold metadata for (cached scan
  // from another order/page), fetch the minimal metadata it needs.
  const fetchedTargetQuery = useQuery({
    queryKey: ['cue-xep-target-episode', slug, targetEpId],
    queryFn: () => getEpisode(slug, targetEpId as string),
    enabled: !!slug && !!targetEpId && !knownTargetEp,
  });
  const targetEp: Episode | undefined =
    knownTargetEp ?? fetchedTargetQuery.data ?? undefined;

  // One key format for playWindow and the playingKey comparisons in the rows.
  const playKey = (episodeId: string, start: number, end: number) =>
    `${episodeId}:${start}:${end}`;

  // Play [start, end] of one episode's retained original; clicking the owning
  // button again stops it.
  const playWindow = (episodeId: string, start: number, end: number) =>
    togglePlayback(
      playKey(episodeId, start, end),
      episodeOriginalUrl(slug, episodeId),
      start,
      end,
    );

  // Titles for the per-episode breakdown. Known metadata first; ids the modal
  // never held (cached scan from another selection/page) are fetched. The
  // target id is excluded -- fetchedTargetQuery above already fetches it.
  const breakdownIds = scanData?.episodeIds ?? [];
  const titleById = new Map<string, string>();
  for (const ep of [...selected, ...pickerEpisodes]) {
    if (!titleById.has(ep.id)) titleById.set(ep.id, ep.title);
  }
  if (targetEp) titleById.set(targetEp.id, targetEp.title);
  const unknownTitleQueries = useQueries({
    queries: breakdownIds
      .filter((id) => id !== targetEpId && !titleById.has(id))
      .map((id) => ({
        queryKey: ['cue-xep-episode-title', slug, id],
        queryFn: () => getEpisode(slug, id),
        enabled: !!slug,
      })),
  });
  for (const q of unknownTitleQueries) {
    if (q.data) titleById.set(q.data.id, q.data.title);
  }

  const toggleEpisode = (ep: Episode) => {
    setSelected((prev) => {
      if (prev.some((p) => p.id === ep.id)) return prev.filter((p) => p.id !== ep.id);
      if (prev.length >= CROSS_EPISODE_MAX) return prev;
      return [...prev, ep];
    });
  };

  // Picker phase
  if (phase === 'picker') {
    return (
      <div className={modalBackdrop} onClick={onClose}>
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Find cues across episodes"
          className={`${modalPanel} w-full max-w-2xl p-5 max-h-[85vh] flex flex-col`}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="text-base font-semibold">Find cues across episodes</h3>
              <p className="text-xs text-muted-foreground">
                Select {CROSS_EPISODE_MIN}-{CROSS_EPISODE_MAX} episodes. Results are shown in the first selected episode's time.
              </p>
            </div>
            <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto border border-border rounded">
            {pickerQuery.isLoading && <div className="p-4"><LoadingSpinner size="sm" /></div>}
            {pickerQuery.error && <p className="p-3 text-sm text-destructive">Could not load episodes.</p>}
            {!pickerQuery.isLoading && pickerEpisodes.length === 0 && (
              <p className="p-3 text-sm text-muted-foreground">No episodes with original audio found.</p>
            )}
            {pickerEpisodes.length > 0 && (
              <ul className="divide-y divide-border">
                {pickerEpisodes.map((ep) => {
                  const checked = selectedIds.includes(ep.id);
                  const atMax = !checked && selectedIds.length >= CROSS_EPISODE_MAX;
                  const rank = selectedIds.indexOf(ep.id);
                  return (
                    <li key={ep.id}>
                      <label
                        className={`flex items-start gap-3 px-3 py-2 cursor-pointer select-none ${atMax ? 'opacity-50 cursor-not-allowed' : 'hover:bg-muted/50'}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={atMax}
                          onChange={() => toggleEpisode(ep)}
                          className="mt-0.5 shrink-0"
                          aria-label={`Select episode ${ep.title}`}
                        />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">
                            {ep.title}
                            {rank === 0 && (
                              <span className="ml-2 px-1.5 py-0.5 text-xs rounded font-medium bg-primary/20 text-primary align-middle">
                                target
                              </span>
                            )}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {ep.published ? formatDate(ep.published) : 'unknown date'}
                            {typeof ep.duration === 'number' && ep.duration > 0
                              ? ` - ${Math.round(ep.duration / 60)} min` : ''}
                          </p>
                        </div>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {pickerTotalPages > 1 && (
            <div className="flex items-center justify-between mt-3 text-sm">
              <button
                type="button"
                className={`px-2 py-1 rounded ${ghostBtn} disabled:opacity-50`}
                onClick={() => setPickerPage((p) => Math.max(0, p - 1))}
                disabled={pickerPage === 0}
              >
                Prev
              </button>
              <span className="text-muted-foreground">
                Page {pickerPage + 1} / {pickerTotalPages}
              </span>
              <button
                type="button"
                className={`px-2 py-1 rounded ${ghostBtn} disabled:opacity-50`}
                onClick={() => setPickerPage((p) => Math.min(pickerTotalPages - 1, p + 1))}
                disabled={pickerPage + 1 >= pickerTotalPages}
              >
                Next
              </button>
            </div>
          )}

          <div className="mt-3 flex items-center justify-between gap-3">
            <div className="text-xs text-muted-foreground">
              {selected.length === 0 && 'Select at least 2 episodes.'}
              {selected.length === 1 && 'Select 1 more episode.'}
              {selected.length >= CROSS_EPISODE_MIN && (
                <>
                  {selected.length} selected{selected.length === CROSS_EPISODE_MAX ? ' (max)' : ''}
                  {targetEp && (
                    <> - results on: <span className="font-medium text-foreground truncate max-w-[180px] inline-block align-bottom">{targetEp.title}</span></>
                  )}
                </>
              )}
            </div>
            <button
              type="button"
              className={`px-3 py-1.5 rounded ${primaryBtn} text-sm disabled:opacity-50`}
              disabled={selected.length < CROSS_EPISODE_MIN}
              onClick={() => setPhase('results')}
            >
              Scan
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Results phase
  return (
    <>
      <div className={modalBackdrop} onClick={onClose}>
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Cross-episode scan results"
          className={`${modalPanel} w-full max-w-2xl p-5 max-h-[85vh] flex flex-col`}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="text-base font-semibold">Cross-episode scan</h3>
              {targetEp && (
                <p className="text-xs text-muted-foreground truncate max-w-xl">
                  Results on: {targetEp.title}
                </p>
              )}
            </div>
            <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
          </div>

          <div className="flex flex-wrap gap-2 mb-3">
            <button
              type="button"
              className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`}
              onClick={() => {
                stopPlayback();
                setPhase('picker');
              }}
            >
              Change episodes
            </button>
            {!scanning && (
              <button
                type="button"
                className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`}
                onClick={() => {
                  stopPlayback();
                  setExpandedKey(null);
                  rescan();
                }}
              >
                Rescan
              </button>
            )}
          </div>

          {scanning && (
            <p className="text-sm text-muted-foreground flex items-center gap-2 mb-3">
              <LoadingSpinner size="sm" inline /> Scanning audio, this can take a minute...
            </p>
          )}
          {!scanning && scanError && (
            <p className="text-sm text-destructive mb-3">{scanError}</p>
          )}
          {!scanning && !scanError && scanData?.status === 'ready' && candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">No recurring segments found.</p>
          )}
          {!scanning && !scanError && candidates.length > 0
            && candidates.some((c) => !c.episodes) && (
            <p className="text-xs text-muted-foreground mb-2">
              Some rows have no per-episode matches yet. Rescan to fill them in.
            </p>
          )}

          {candidates.length > 0 && (
            <ul className="flex-1 overflow-y-auto divide-y divide-border border border-border rounded">
              {candidates.map((c) => {
                const rowKey = `${c.start}-${c.end}`;
                const rowPlayKey = targetEpId
                  ? playKey(targetEpId, c.start, c.end)
                  : undefined;
                const expanded = expandedKey === rowKey;
                return (
                  <li key={rowKey} className="px-3 py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                      <button
                        type="button"
                        className={`shrink-0 p-1.5 rounded ${ghostBtn}`}
                        onClick={() => targetEpId && playWindow(targetEpId, c.start, c.end)}
                        disabled={!targetEpId}
                        title={playingKey === rowPlayKey ? 'Stop' : 'Play candidate'}
                        aria-label={playingKey === rowPlayKey ? 'Stop candidate' : 'Play candidate'}
                      >
                        {playingKey === rowPlayKey ? <Square size={14} /> : <Play size={14} />}
                      </button>
                      <div className="flex-1 min-w-0">
                        <span className="font-mono text-sm">
                          {formatTimestamp(c.start)} - {formatTimestamp(c.end)}
                        </span>
                        <span className="ml-2 text-xs text-muted-foreground">
                          {(c.end - c.start).toFixed(2)}s
                        </span>
                        {c.episodeMatches != null && (
                          <span className="ml-2 px-1.5 py-0.5 text-xs rounded font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                            {/* episodeMatches counts SIBLINGS; the target is always a match too, so +1. */}
                            in {c.episodeMatches + 1} of {episodeCount} eps
                          </span>
                        )}
                      </div>
                      {c.episodes && (
                        <button
                          type="button"
                          className="shrink-0 p-1 rounded text-muted-foreground hover:text-foreground"
                          onClick={() => setExpandedKey(expanded ? null : rowKey)}
                          aria-label={expanded ? 'Hide per-episode matches' : 'Show per-episode matches'}
                          aria-expanded={expanded}
                        >
                          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                        </button>
                      )}
                      <button
                        type="button"
                        className={`shrink-0 px-3 py-1.5 rounded ${primaryBtn} text-xs`}
                        onClick={() => setSeed(c)}
                      >
                        Make template
                      </button>
                    </div>
                    {expanded && c.episodes && (
                      <ul className="mt-2 ml-9 space-y-2">
                        {c.episodes.map((epm) => (
                          <li key={epm.episodeId} className="text-xs">
                            <span className="font-medium">
                              {titleById.get(epm.episodeId) ?? epm.episodeId}
                            </span>
                            <span className="ml-2 text-muted-foreground">
                              {epm.matchCount === 0
                                ? 'not found'
                                : `${epm.matchCount} match${epm.matchCount === 1 ? '' : 'es'}`}
                            </span>
                            {epm.matches.length > 0 && (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {epm.matches.map((m) => {
                                  const mKey = playKey(epm.episodeId, m.start, m.end);
                                  return (
                                    <button
                                      key={mKey}
                                      type="button"
                                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-border font-mono hover:bg-muted/50"
                                      onClick={() => playWindow(epm.episodeId, m.start, m.end)}
                                      aria-label={playingKey === mKey
                                        ? `Stop match at ${formatTimestamp(m.start)}`
                                        : `Play match at ${formatTimestamp(m.start)}`}
                                    >
                                      {playingKey === mKey ? <Square size={10} /> : <Play size={10} />}
                                      {formatTimestamp(m.start)}
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {audioElement}
        </div>
      </div>

      {seed && targetEp && (
        <CueMarkModal
          podcastSlug={slug}
          episodeId={targetEp.id}
          episodeTitle={targetEp.title}
          episodeDuration={targetEp.duration ?? 0}
          initialStart={seed.start}
          initialEnd={seed.end}
          captureMinSeconds={captureMinSeconds}
          captureMaxSeconds={captureMaxSeconds}
          captureMaxIntroSeconds={captureMaxIntroSeconds}
          captureMaxOutroSeconds={captureMaxOutroSeconds}
          onClose={() => setSeed(null)}
          onSaved={onSaved}
          onFinalSave={() => setSeed(null)}
        />
      )}
    </>
  );
}
