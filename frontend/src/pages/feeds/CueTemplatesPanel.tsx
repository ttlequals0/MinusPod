import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import CueMarkModal from '../../components/CueMarkModal';
import {
  deleteCueTemplate,
  listCueTemplates,
  scanEpisodeCues,
  updateCueTemplate,
  type CueScanResponse,
  type CueTemplate,
} from '../../api/cueTemplates';
import { getEpisode, getEpisodes } from '../../api/feeds';
import type { Episode } from '../../api/types';
import { formatTime } from '../../utils/adReviewHelpers';

const PICKER_PAGE_SIZE = 50;

interface Props {
  slug: string;
}

// Close-on-Escape for the lightweight modals below.
function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
}

// Per-feed cue template management. Templates take precedence over the global
// spectral cue detector when at least one is enabled for the feed.
function CueTemplatesPanel({ slug }: Props) {
  const queryClient = useQueryClient();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [openModal, setOpenModal] = useState<{ episodeId: string; episodeTitle: string; duration: number } | null>(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const templatesQuery = useQuery({
    queryKey: ['cue-templates', slug],
    queryFn: () => listCueTemplates(slug),
    enabled: !!slug,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { label?: string; enabled?: boolean } }) =>
      updateCueTemplate(id, patch),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof Error ? e.message : 'Update failed'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteCueTemplate(id),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof Error ? e.message : 'Delete failed'),
  });

  const templates: CueTemplate[] = useMemo(
    () => templatesQuery.data ?? [],
    [templatesQuery.data],
  );

  const handleToggle = (template: CueTemplate) => {
    setActionError(null);
    updateMutation.mutate({ id: template.id, patch: { enabled: !template.enabled } });
  };

  const startRename = (template: CueTemplate) => {
    setActionError(null);
    setEditingId(template.id);
    setEditValue(template.label);
  };

  const commitRename = (template: CueTemplate) => {
    const next = editValue.trim();
    setEditingId(null);
    if (next && next !== template.label) {
      updateMutation.mutate({ id: template.id, patch: { label: next } });
    }
  };

  const handlePickEpisode = async (ep: Episode) => {
    setActionError(null);
    try {
      // Trust the list-endpoint flag when present, fall back to a detail fetch.
      let originalAvailable = ep.hasOriginalAudio;
      if (originalAvailable === undefined) {
        const detail = await getEpisode(slug, ep.id);
        originalAvailable = detail.hasOriginalAudio;
      }
      if (!originalAvailable) {
        setActionError('That episode has no retained original audio. Pick a processed episode whose original audio was kept.');
        return;
      }
      setPickerOpen(false);
      setOpenModal({ episodeId: ep.id, episodeTitle: ep.title, duration: ep.duration ?? 0 });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not open this episode');
    }
  };

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Audio Cue Templates"
        subtitle="User-marked ding/stinger samples. When at least one is enabled the matcher snaps ad edges to these cues."
        defaultOpen={false}
        storageKey={`feed-cue-templates-${slug}`}
      >
        <div className="flex items-center justify-between mb-3 gap-3">
          <p className="text-sm text-muted-foreground">
            Mark a short non-spoken cue (chime, stinger) from one episode and the
            matcher finds it on every other episode. Per-feed only.
          </p>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              className="px-3 py-1.5 rounded border border-input hover:bg-muted text-sm"
              onClick={() => setScanOpen(true)}
              disabled={templates.length === 0}
              title={templates.length === 0 ? 'Mark at least one cue first' : 'Run all enabled templates against an episode'}
            >
              Test on episode
            </button>
            <button
              type="button"
              className="px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 text-sm"
              onClick={() => { setActionError(null); setPickerOpen(true); }}
            >
              + Mark cue
            </button>
          </div>
        </div>

        {actionError && <p className="text-sm text-destructive mb-2">{actionError}</p>}
        {templatesQuery.isLoading && <LoadingSpinner size="sm" className="my-2" />}
        {templatesQuery.error && (
          <p className="text-sm text-destructive">Could not load cue templates.</p>
        )}

        {!templatesQuery.isLoading && templates.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No cue templates yet. Mark one from a recent episode to start.
          </p>
        )}

        {templates.length > 0 && (
          <ul className="divide-y divide-border border rounded">
            {templates.map((t) => (
              <li key={t.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={t.enabled}
                  onChange={() => handleToggle(t)}
                  aria-label={`Enable cue ${t.label}`}
                />
                <div className="flex-1 min-w-0">
                  {editingId === t.id ? (
                    <input
                      type="text"
                      autoFocus
                      maxLength={80}
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={() => commitRename(t)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitRename(t);
                        if (e.key === 'Escape') setEditingId(null);
                      }}
                      className="w-full border rounded px-2 py-1 bg-background text-sm"
                      aria-label="Cue label"
                    />
                  ) : (
                    <>
                      <p className="font-medium truncate">
                        {t.label}
                        {t.scope === 'network' && (
                          <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-secondary text-secondary-foreground align-middle">
                            NETWORK
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t.durationS.toFixed(2)}s - marked at {formatTime(t.sourceOffsetS)}
                        {t.sourceEpisodeId ? ` of episode ${t.sourceEpisodeId.slice(0, 8)}` : ''}
                      </p>
                    </>
                  )}
                </div>
                {editingId !== t.id && (
                  <>
                    <button
                      type="button"
                      className="text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => startRename(t)}
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      className="text-xs text-destructive hover:text-destructive/80"
                      onClick={() => deleteMutation.mutate(t.id)}
                    >
                      Delete
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </CollapsibleSection>

      {pickerOpen && (
        <EpisodePicker slug={slug} onClose={() => setPickerOpen(false)} onPick={handlePickEpisode} />
      )}

      {openModal && (
        <CueMarkModal
          podcastSlug={slug}
          episodeId={openModal.episodeId}
          episodeTitle={openModal.episodeTitle}
          episodeDuration={openModal.duration}
          onClose={() => setOpenModal(null)}
          onSaved={invalidate}
        />
      )}

      {scanOpen && <CueScanModal slug={slug} onClose={() => setScanOpen(false)} />}
    </div>
  );
}

interface EpisodePickerProps {
  slug: string;
  onClose: () => void;
  onPick: (ep: Episode) => void;
}

function EpisodePicker({ slug, onClose, onPick }: EpisodePickerProps) {
  useEscape(onClose);
  const [statusFilter, setStatusFilter] = useState<'all' | 'completed'>('completed');
  const [onlyWithOriginal, setOnlyWithOriginal] = useState(true);
  const [page, setPage] = useState(0);

  const query = useQuery({
    queryKey: ['cue-template-picker', slug, statusFilter, page],
    queryFn: () =>
      getEpisodes(slug, {
        limit: PICKER_PAGE_SIZE,
        offset: page * PICKER_PAGE_SIZE,
        status: statusFilter === 'all' ? undefined : statusFilter,
        sortBy: 'published',
        sortDir: 'desc',
      }),
    enabled: !!slug,
  });

  const allEpisodes = query.data?.episodes ?? [];
  const episodes = onlyWithOriginal
    ? allEpisodes.filter((ep) => ep.hasOriginalAudio !== false)
    : allEpisodes;
  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PICKER_PAGE_SIZE));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Pick an episode"
        className="bg-background text-foreground rounded-lg shadow-xl w-full max-w-2xl p-5 max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="text-base font-semibold">Pick an episode</h3>
            <p className="text-xs text-muted-foreground">
              Choose any episode that still has its original audio retained. Cues
              from any episode apply to the whole feed.
            </p>
          </div>
          <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-3 mb-3 text-sm">
          <div className="flex items-center gap-2">
            <label htmlFor="cue-picker-filter">Show:</label>
            <select
              id="cue-picker-filter"
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value as 'all' | 'completed'); setPage(0); }}
              className="px-2 py-1 text-sm bg-secondary border border-border rounded"
            >
              <option value="completed">Processed only</option>
              <option value="all">All episodes</option>
            </select>
          </div>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={onlyWithOriginal} onChange={(e) => setOnlyWithOriginal(e.target.checked)} />
            With original audio only
          </label>
        </div>

        <div className="flex-1 overflow-y-auto border rounded">
          {query.isLoading && <div className="p-4"><LoadingSpinner size="sm" /></div>}
          {query.error && <p className="p-3 text-sm text-destructive">Could not load episodes.</p>}
          {!query.isLoading && episodes.length === 0 && (
            <p className="p-3 text-sm text-muted-foreground">No episodes match this filter.</p>
          )}
          {episodes.length > 0 && (
            <ul className="divide-y divide-border">
              {episodes.map((ep) => {
                const noOriginal = ep.hasOriginalAudio === false;
                return (
                  <li key={ep.id}>
                    <button
                      type="button"
                      onClick={() => onPick(ep)}
                      disabled={noOriginal}
                      className={`w-full text-left px-3 py-2 ${noOriginal ? 'opacity-50 cursor-not-allowed' : 'hover:bg-muted/50'}`}
                      title={noOriginal ? 'Original audio not retained for this episode' : undefined}
                    >
                      <p className="text-sm font-medium truncate">{ep.title}</p>
                      <p className="text-xs text-muted-foreground">
                        {ep.published ? new Date(ep.published).toLocaleDateString() : 'unknown date'}
                        {' - '}{ep.status}
                        {typeof ep.duration === 'number' && ep.duration > 0 ? ` - ${Math.round(ep.duration / 60)} min` : ''}
                        {noOriginal ? ' - no original audio' : ''}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-3 text-sm">
            <button
              type="button"
              className="px-2 py-1 border rounded disabled:opacity-50"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              Prev
            </button>
            <span className="text-muted-foreground">
              Page {page + 1} / {totalPages} ({total} episodes)
            </span>
            <button
              type="button"
              className="px-2 py-1 border rounded disabled:opacity-50"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page + 1 >= totalPages}
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

interface CueScanModalProps {
  slug: string;
  onClose: () => void;
}

// Test-mode panel: pick an episode, optionally override the score threshold,
// run every enabled template against the episode and show peak score + match
// times per template. No DB writes; pure diagnostic.
function CueScanModal({ slug, onClose }: CueScanModalProps) {
  useEscape(onClose);
  const [picking, setPicking] = useState(true);
  const [selectedEpisode, setSelectedEpisode] = useState<Episode | null>(null);
  const [scoreOverride, setScoreOverride] = useState<string>('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CueScanResponse | null>(null);

  const runScan = async (ep: Episode, override?: number) => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await scanEpisodeCues(slug, ep.id, override);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setRunning(false);
    }
  };

  const onPick = async (ep: Episode) => {
    try {
      const detail = await getEpisode(slug, ep.id);
      if (detail.hasOriginalAudio === false) {
        setError('That episode has no retained original audio.');
        return;
      }
      setPicking(false);
      setSelectedEpisode(ep);
      await runScan(ep);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load episode');
    }
  };

  if (picking) {
    return <EpisodePicker slug={slug} onClose={onClose} onPick={onPick} />;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Cue scan"
        className="bg-background text-foreground rounded-lg shadow-xl w-full max-w-3xl p-5 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="text-base font-semibold">Cue scan</h3>
            <p className="text-xs text-muted-foreground truncate max-w-xl">{selectedEpisode?.title}</p>
          </div>
          <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-wrap items-end gap-3 mb-4">
          <div>
            <label className="block text-xs text-muted-foreground" htmlFor="score-override">
              Score threshold (optional)
            </label>
            <input
              id="score-override"
              type="number"
              min={0}
              max={0.99}
              step={0.05}
              placeholder="default"
              value={scoreOverride}
              onChange={(e) => setScoreOverride(e.target.value)}
              className="w-28 border rounded px-2 py-1 bg-background text-sm font-mono"
            />
          </div>
          <button
            type="button"
            className="px-3 py-1.5 rounded border border-input hover:bg-muted text-sm"
            onClick={() => {
              if (!selectedEpisode) return;
              const n = scoreOverride.trim() === '' ? undefined : Number(scoreOverride);
              if (n !== undefined && (Number.isNaN(n) || n < 0 || n > 0.99)) {
                setError('threshold must be between 0 and 0.99');
                return;
              }
              runScan(selectedEpisode, n);
            }}
            disabled={running}
          >
            {running ? 'Scanning...' : 'Rescan'}
          </button>
          <button
            type="button"
            className="px-3 py-1.5 rounded border border-input hover:bg-muted text-sm"
            onClick={() => { setPicking(true); setResult(null); setSelectedEpisode(null); }}
          >
            Pick different episode
          </button>
        </div>

        {error && <p className="text-sm text-destructive mb-3">{error}</p>}
        {running && <LoadingSpinner size="sm" className="my-3" />}

        {result && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Threshold {result.thresholdUsed.toFixed(2)} - scan {result.elapsedSeconds.toFixed(1)}s
            </p>
            <ul className="divide-y divide-border border rounded">
              {result.templates.map((t) => {
                const passed = t.peakScore >= result.thresholdUsed;
                return (
                  <li key={t.id} className="p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-medium text-sm truncate">{t.label}</p>
                        <p className="text-xs text-muted-foreground">
                          {t.durationS.toFixed(2)}s - template #{t.id}
                        </p>
                      </div>
                      <div className="text-right shrink-0">
                        <p className={`text-sm font-mono ${passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
                          peak {t.peakScore.toFixed(3)}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {t.matchCount} match{t.matchCount === 1 ? '' : 'es'}
                        </p>
                      </div>
                    </div>
                    {t.matches.length > 0 && (
                      <ul className="mt-2 text-xs grid grid-cols-2 sm:grid-cols-3 gap-1 max-h-32 overflow-y-auto">
                        {t.matches.slice(0, 30).map((m, i) => (
                          <li key={i} className="font-mono">
                            {formatTime(m.start)} - {m.score.toFixed(3)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default CueTemplatesPanel;
