import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import CueMarkModal from '../../components/CueMarkModal';
import {
  CUE_TYPE_OPTIONS,
  cueTemplateExportUrl,
  deleteCueTemplate,
  importCueTemplate,
  listCueTemplates,
  previewCueTemplate,
  scanEpisodeCues,
  updateCueTemplate,
  type CueScanResponse,
  type CueTemplate,
  type CueTemplateScope,
  type CueTemplateType,
} from '../../api/cueTemplates';
import { getCueFeedAdvisory } from '../../api/cueDetections';
import { getEpisode, getEpisodes, getFeed, getFeeds } from '../../api/feeds';
import { getSettings } from '../../api/settings';
import type { Feed } from '../../api/types';
import type { Episode } from '../../api/types';
import { formatTime } from '../../utils/adReviewHelpers';

const PICKER_PAGE_SIZE = 50;

// Design-system recipes shared by the panel and its modals (match the app's
// confirm/edit modals and form controls; theme-aware in dark mode).
const ghostBtn = 'border border-border hover:bg-accent transition-colors';
const primaryBtn = 'bg-primary text-primary-foreground hover:bg-primary/90 transition-colors';
const fieldCls = 'rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring';
const modalBackdrop = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4';
const modalPanel = 'bg-card text-foreground rounded-lg border border-border shadow-xl';

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
  const [editValue, setEditValue] = useState<CueTemplateType>('ad_break_boundary');
  const [actionError, setActionError] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);
  // Set on Escape so the unmount-triggered blur cancels instead of committing.
  const editCancelledRef = useRef(false);
  const [verifyState, setVerifyState] = useState<{ label: string; checked: number; matched: number } | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [promoteState, setPromoteState] = useState<{ template: CueTemplate; feeds: Feed[] } | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const templatesQuery = useQuery({
    queryKey: ['cue-templates', slug],
    queryFn: () => listCueTemplates(slug),
    enabled: !!slug,
  });

  // The feed's network id gates the "promote to network" action.
  const feedQuery = useQuery({
    queryKey: ['feed', slug],
    queryFn: () => getFeed(slug),
    enabled: !!slug,
  });
  const networkId = feedQuery.data?.networkIdOverride || feedQuery.data?.networkId || null;

  // Capture length bounds come from settings so the mark dialog honors the
  // configured audio_cue_capture_min/max_seconds.
  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const captureMinSeconds = settingsQuery.data?.audioCueCaptureMinSeconds?.value ?? 0.2;
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 4;

  // Per-feed cue health, so the user can judge a feed's cues before enabling
  // cue-pair synthesis (#350 follow-up). Empty until episodes are processed.
  const advisoryQuery = useQuery({
    queryKey: ['cue-advisory', slug],
    queryFn: () => getCueFeedAdvisory(slug),
    enabled: !!slug,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { cueType?: CueTemplateType; enabled?: boolean; scope?: CueTemplateScope; networkId?: string } }) =>
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

  const startEditType = (template: CueTemplate) => {
    setActionError(null);
    setEditingId(template.id);
    setEditValue(template.cueType);
  };

  const commitType = (template: CueTemplate) => {
    setEditingId(null);
    if (editCancelledRef.current) {
      editCancelledRef.current = false;
      return;
    }
    if (editValue !== template.cueType) {
      updateMutation.mutate({ id: template.id, patch: { cueType: editValue } });
    }
  };

  const handlePromote = async (template: CueTemplate) => {
    setActionError(null);
    if (template.scope === 'network') {
      // Demotion has no blast radius; apply immediately.
      updateMutation.mutate({ id: template.id, patch: { scope: 'podcast' } });
      return;
    }
    if (!networkId) return;
    // Promotion applies the cue to every feed on the network -- show which ones
    // before committing.
    try {
      const feeds = await getFeeds();
      const onNetwork = feeds.filter((f) => (f.networkIdOverride || f.networkId) === networkId);
      setPromoteState({ template, feeds: onNetwork });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not load network feeds');
    }
  };

  const confirmPromote = () => {
    if (!promoteState || !networkId) return;
    updateMutation.mutate({ id: promoteState.template.id, patch: { scope: 'network', networkId } });
    setPromoteState(null);
  };

  // After a cue is saved, confirm it actually recurs by previewing it against a
  // few other recent episodes -- a bad or loose bracket shows up immediately.
  const runAutoVerify = async (template: CueTemplate) => {
    setVerifyState(null);
    setVerifying(true);
    try {
      const resp = await getEpisodes(slug, {
        limit: 12, status: 'completed', sortBy: 'published', sortDir: 'desc',
      });
      const candidates = (resp.episodes || [])
        .filter((ep) => ep.hasOriginalAudio !== false && ep.id !== template.sourceEpisodeId)
        .slice(0, 3);
      if (!candidates.length) return;
      let matched = 0;
      for (const ep of candidates) {
        try {
          const res = await previewCueTemplate(slug, ep.id, template.id);
          if (res.matches.length > 0) matched += 1;
        } catch {
          /* skip an episode that fails to scan */
        }
      }
      setVerifyState({ label: template.label, checked: candidates.length, matched });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Auto-verify failed');
    } finally {
      setVerifying(false);
    }
  };

  const importMutation = useMutation({
    mutationFn: (file: File) => importCueTemplate(slug, file),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof Error ? e.message : 'Import failed'),
  });

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setActionError(null);
    importMutation.mutate(file);
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
        setActionError('That episode has no retained original audio. Pick a processed one that kept it.');
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
        subtitle="A recurring ding or stinger the matcher snaps ad edges to."
        defaultOpen={false}
        storageKey={`feed-cue-templates-${slug}`}
      >
        <input
          ref={importInputRef}
          type="file"
          accept=".zip,application/zip"
          className="hidden"
          onChange={handleImportFile}
        />
        <div className="flex flex-wrap gap-2 mb-3">
          <button
            type="button"
            className={`flex-1 sm:flex-none basis-0 sm:basis-auto whitespace-nowrap px-3 py-1.5 rounded ${ghostBtn} text-sm`}
            onClick={() => importInputRef.current?.click()}
            disabled={importMutation.isPending}
            title="Import a cue template zip exported from another install"
          >
            {importMutation.isPending ? 'Importing...' : 'Import'}
          </button>
          <button
            type="button"
            className={`flex-1 sm:flex-none basis-0 sm:basis-auto whitespace-nowrap px-3 py-1.5 rounded ${ghostBtn} text-sm disabled:opacity-50`}
            onClick={() => setScanOpen(true)}
            disabled={templates.length === 0}
            title={templates.length === 0 ? 'Mark at least one cue first' : 'Run all enabled templates against an episode'}
          >
            Test on episode
          </button>
          <button
            type="button"
            className={`flex-1 sm:flex-none basis-0 sm:basis-auto whitespace-nowrap px-3 py-1.5 rounded ${primaryBtn} text-sm`}
            onClick={() => { setActionError(null); setPickerOpen(true); }}
          >
            + Mark cue
          </button>
        </div>

        {actionError && <p className="text-sm text-destructive mb-2">{actionError}</p>}
        {verifying && (
          <p className="text-sm text-muted-foreground mb-2 flex items-center gap-2">
            <LoadingSpinner size="sm" inline /> Checking recent episodes...
          </p>
        )}
        {verifyState && !verifying && (
          <p className={`text-sm mb-2 ${verifyState.matched > 0 ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
            Cue "{verifyState.label}" matched {verifyState.matched} of {verifyState.checked} recent
            episode{verifyState.checked === 1 ? '' : 's'}.
            {verifyState.matched === 0 ? ' No matches yet - it may not recur, or the bracket is loose.' : ''}
          </p>
        )}
        {templatesQuery.isLoading && <LoadingSpinner size="sm" className="my-2" />}
        {templatesQuery.error && (
          <p className="text-sm text-destructive">Could not load cue templates.</p>
        )}

        {!templatesQuery.isLoading && templates.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No cues yet. Mark one to start.
          </p>
        )}

        {templates.length > 0 && (
          <ul className="divide-y divide-border border border-border rounded">
            {templates.map((t) => (
              <li key={t.id} className="flex flex-col gap-2 px-3 py-2 text-sm sm:flex-row sm:items-center sm:gap-3">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    onChange={() => handleToggle(t)}
                    disabled={t.owned === false}
                    title={t.owned === false ? 'Managed on the feed that created it' : undefined}
                    aria-label={`Enable cue ${t.label}`}
                  />
                  <div className="flex-1 min-w-0">
                  {editingId === t.id ? (
                    <select
                      autoFocus
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value as CueTemplateType)}
                      onBlur={() => commitType(t)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') e.currentTarget.blur();
                        if (e.key === 'Escape') {
                          editCancelledRef.current = true;
                          e.currentTarget.blur();
                        }
                      }}
                      className={`w-full px-3 py-1.5 ${fieldCls} text-sm`}
                      aria-label="Cue type"
                    >
                      {CUE_TYPE_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  ) : (
                    <>
                      <p className="font-medium truncate">
                        {t.label}
                        {t.scope === 'network' && (
                          <span className="ml-2 px-2 py-0.5 rounded text-xs font-medium bg-purple-500/20 text-purple-600 dark:text-purple-400 align-middle">
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
                </div>
                {editingId !== t.id && (
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-7 sm:pl-0 sm:shrink-0">
                    <a
                      className="text-xs text-muted-foreground hover:text-foreground"
                      href={cueTemplateExportUrl(t.id)}
                      title="Download this cue as a portable zip"
                    >
                      Export
                    </a>
                    {t.owned === false ? (
                      <span className="text-xs text-muted-foreground italic">
                        Shared from this network
                      </span>
                    ) : (
                      <>
                        {(t.scope === 'network' || networkId) && (
                          <button
                            type="button"
                            className="text-xs text-muted-foreground hover:text-foreground"
                            onClick={() => handlePromote(t)}
                            title={
                              t.scope === 'network'
                                ? 'Limit this cue to this feed only'
                                : `Apply this cue to every feed on network "${networkId}"`
                            }
                          >
                            {t.scope === 'network' ? 'Make podcast-only' : 'Promote to network'}
                          </button>
                        )}
                        <button
                          type="button"
                          className="text-xs text-muted-foreground hover:text-foreground"
                          onClick={() => startEditType(t)}
                        >
                          Change type
                        </button>
                        {confirmDeleteId === t.id ? (
                          <>
                            <button
                              type="button"
                              className="text-xs text-destructive font-medium"
                              onClick={() => { deleteMutation.mutate(t.id); setConfirmDeleteId(null); }}
                            >
                              Confirm
                            </button>
                            <button
                              type="button"
                              className="text-xs text-muted-foreground hover:text-foreground"
                              onClick={() => setConfirmDeleteId(null)}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="text-xs text-destructive hover:text-destructive/80"
                            onClick={() => setConfirmDeleteId(t.id)}
                          >
                            Delete
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {advisoryQuery.data && advisoryQuery.data.total > 0 && (
          <div className="mt-4 pt-4 border-t border-border">
            <h4 className="text-sm font-semibold text-foreground mb-1">Cue health</h4>
            <p className="text-xs text-muted-foreground mb-3">
              How this feed's cues have done across processed episodes. Use it to
              decide whether to enable cue-pair synthesis.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {([
                ['Detections', String(advisoryQuery.data.total)],
                ['Paired / Snapped', `${advisoryQuery.data.paired} / ${advisoryQuery.data.snapped}`],
                ['Confirm rate', advisoryQuery.data.confirmRate != null
                  ? `${Math.round(advisoryQuery.data.confirmRate * 100)}%` : '--'],
                ['Score range', advisoryQuery.data.minScore != null
                  ? `${Math.round(advisoryQuery.data.minScore * 100)}-${Math.round((advisoryQuery.data.maxScore ?? 0) * 100)}%`
                  : '--'],
              ] as const).map(([label, value]) => (
                <div key={label} className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                  <div className="text-xs text-muted-foreground">{label}</div>
                  <div className="text-sm font-semibold text-foreground">{value}</div>
                </div>
              ))}
            </div>
          </div>
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
          onFinalSave={runAutoVerify}
          captureMinSeconds={captureMinSeconds}
          captureMaxSeconds={captureMaxSeconds}
        />
      )}

      {scanOpen && <CueScanModal slug={slug} onClose={() => setScanOpen(false)} />}

      {promoteState && (
        <div className={modalBackdrop} onClick={() => setPromoteState(null)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Promote cue to network"
            className={`${modalPanel} w-full max-w-md p-5`}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold mb-2">Promote to network</h3>
            <p className="text-sm text-muted-foreground mb-3">
              Cue "{promoteState.template.label}" will start matching every feed on
              network "{networkId}" ({promoteState.feeds.length}):
            </p>
            <ul className="text-sm border border-border rounded divide-y divide-border max-h-48 overflow-y-auto mb-4">
              {promoteState.feeds.map((f) => (
                <li key={f.slug} className="px-3 py-1.5 truncate">{f.title || f.slug}</li>
              ))}
            </ul>
            <div className="flex justify-end gap-2">
              <button type="button" className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`} onClick={() => setPromoteState(null)}>
                Cancel
              </button>
              <button type="button" className={`px-3 py-1.5 rounded ${primaryBtn} text-sm`} onClick={confirmPromote}>
                Promote
              </button>
            </div>
          </div>
        </div>
      )}
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
  const [page, setPage] = useState(0);

  // Cues can only be marked on a processed episode whose original audio is
  // still retained, so the picker is fixed to that set.
  const query = useQuery({
    queryKey: ['cue-template-picker', slug, page],
    queryFn: () =>
      getEpisodes(slug, {
        limit: PICKER_PAGE_SIZE,
        offset: page * PICKER_PAGE_SIZE,
        status: 'completed',
        sortBy: 'published',
        sortDir: 'desc',
      }),
    enabled: !!slug,
  });

  const allEpisodes = query.data?.episodes ?? [];
  const episodes = allEpisodes.filter((ep) => ep.hasOriginalAudio !== false);
  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PICKER_PAGE_SIZE));

  return (
    <div className={modalBackdrop} onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Pick an episode"
        className={`${modalPanel} w-full max-w-2xl p-5 max-h-[85vh] flex flex-col`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="text-base font-semibold">Pick an episode</h3>
            <p className="text-xs text-muted-foreground">
              Any episode with retained original audio. A cue applies to the whole feed.
            </p>
          </div>
          <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto border border-border rounded">
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
              className={`px-2 py-1 rounded ${ghostBtn} disabled:opacity-50`}
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
              className={`px-2 py-1 rounded ${ghostBtn} disabled:opacity-50`}
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
    <div className={modalBackdrop} onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Cue scan"
        className={`${modalPanel} w-full max-w-3xl p-5 max-h-[90vh] overflow-y-auto`}
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
              className={`w-28 px-3 py-1.5 ${fieldCls} text-sm font-mono`}
            />
          </div>
          <button
            type="button"
            className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`}
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
            className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`}
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
            <ul className="divide-y divide-border border border-border rounded">
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
                        <p className={`text-sm font-mono ${passed ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'}`}>
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
