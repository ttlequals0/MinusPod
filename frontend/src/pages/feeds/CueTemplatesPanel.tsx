import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X, Play, Square } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import CueMarkModal from '../../components/CueMarkModal';
import {
  CUE_TYPE_OPTIONS,
  crossEpisodeScan,
  cueTemplateAudioUrl,
  cueTemplateExportUrl,
  deleteCueTemplate,
  importCueTemplate,
  listCueTemplates,
  optimizeCueWindow,
  previewCueTemplate,
  scanEpisodeCues,
  suggestCueThreshold,
  updateCueTemplate,
  type CrossEpisodeCandidate,
  type CrossEpisodeScanResponse,
  type CueScanResponse,
  type CueTemplate,
  type CueTemplateScope,
  type CueTemplateType,
  type CueWindowOptimizeResponse,
  type ThresholdSuggestResponse,
} from '../../api/cueTemplates';
import { getCueFeedAdvisory } from '../../api/cueDetections';
import { getEpisode, getEpisodes, getFeed, getFeeds, updateFeed, CUE_SCORE_MAX } from '../../api/feeds';
import { getSettings } from '../../api/settings';
import type { Feed } from '../../api/types';
import type { Episode } from '../../api/types';
import { formatTime } from '../../utils/adReviewHelpers';
import { formatTimestamp } from '../../utils/format';

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
  const [crossEpisodeScanOpen, setCrossEpisodeScanOpen] = useState(false);
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
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<number | null>(null);
  const [editingThresholdId, setEditingThresholdId] = useState<number | null>(null);
  const [editThresholdValue, setEditThresholdValue] = useState<string>('');
  const thresholdCancelledRef = useRef(false);
  // Template whose optimize-window panel is expanded (one at a time).
  const [optimizeId, setOptimizeId] = useState<number | null>(null);

  const togglePlay = (t: CueTemplate) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingId === t.id) {
      audio.pause();
      setPlayingId(null);
      return;
    }
    audio.src = cueTemplateAudioUrl(t.id);
    audio.play().then(() => setPlayingId(t.id)).catch(() => setPlayingId(null));
  };

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
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 10;
  const captureMaxIntroSeconds = settingsQuery.data?.audioCueCaptureMaxIntroSeconds?.value ?? 60;
  const captureMaxOutroSeconds = settingsQuery.data?.audioCueCaptureMaxOutroSeconds?.value ?? 60;

  // Per-feed cue health, so the user can judge a feed's cues before enabling
  // cue-pair synthesis (#350 follow-up). Empty until episodes are processed.
  const advisoryQuery = useQuery({
    queryKey: ['cue-advisory', slug],
    queryFn: () => getCueFeedAdvisory(slug),
    enabled: !!slug,
  });

  // Gate for the "Find across episodes" button: needs >= 2 episodes with
  // retained originals. Shares the picker's page-0 query (cached), and only
  // disables once loaded data confirms the shortfall, so the button doesn't
  // flash disabled while the query is in flight.
  const eligibleQuery = useQuery({
    queryKey: ['cue-template-picker', slug, 0],
    queryFn: () =>
      getEpisodes(slug, {
        limit: PICKER_PAGE_SIZE,
        offset: 0,
        status: 'completed',
        sortBy: 'published',
        sortDir: 'desc',
      }),
    enabled: !!slug,
    staleTime: 60_000,
  });
  const eligibleCount = (eligibleQuery.data?.episodes ?? []).filter(
    (ep) => ep.hasOriginalAudio !== false,
  ).length;
  const crossEpisodeEligible = !eligibleQuery.data || eligibleCount >= 2;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { cueType?: CueTemplateType; enabled?: boolean; scope?: CueTemplateScope; networkId?: string; scoreThreshold?: number | null } }) =>
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

  const startEditThreshold = (template: CueTemplate) => {
    setActionError(null);
    setEditingThresholdId(template.id);
    setEditThresholdValue(
      template.scoreThreshold != null ? String(template.scoreThreshold) : '',
    );
  };

  const commitThreshold = (template: CueTemplate) => {
    setEditingThresholdId(null);
    if (thresholdCancelledRef.current) {
      thresholdCancelledRef.current = false;
      return;
    }
    const trimmed = editThresholdValue.trim();
    if (trimmed === '') {
      if (template.scoreThreshold != null) {
        updateMutation.mutate({ id: template.id, patch: { scoreThreshold: null } });
      }
      return;
    }
    const val = parseFloat(trimmed);
    if (isNaN(val) || val < 0.30 || val > 0.99) {
      setActionError('Score threshold must be a number between 0.30 and 0.99');
      return;
    }
    if (val !== template.scoreThreshold) {
      updateMutation.mutate({ id: template.id, patch: { scoreThreshold: val } });
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
        <audio ref={audioRef} onEnded={() => setPlayingId(null)} className="hidden" />
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
            className={`flex-1 sm:flex-none basis-0 sm:basis-auto whitespace-nowrap px-3 py-1.5 rounded ${ghostBtn} text-sm disabled:opacity-50`}
            onClick={() => { setActionError(null); setCrossEpisodeScanOpen(true); }}
            disabled={!crossEpisodeEligible}
            title={!crossEpisodeEligible ? 'Need at least 2 episodes with original audio' : 'Find recurring cue sounds across multiple episodes'}
          >
            Find across episodes
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
              <li key={t.id} className="px-3 py-2 text-sm">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
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
                    {t.hasAudio !== false && (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                        onClick={() => togglePlay(t)}
                        title={playingId === t.id ? 'Stop' : 'Play this cue'}
                        aria-label={playingId === t.id ? `Stop cue ${t.label}` : `Play cue ${t.label}`}
                      >
                        {playingId === t.id ? <Square className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                        {playingId === t.id ? 'Stop' : 'Play'}
                      </button>
                    )}
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
                        {editingThresholdId === t.id ? (
                          <input
                            type="number"
                            autoFocus
                            min={0.30}
                            max={0.99}
                            step={0.01}
                            value={editThresholdValue}
                            onChange={(e) => setEditThresholdValue(e.target.value)}
                            onBlur={() => commitThreshold(t)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') e.currentTarget.blur();
                              if (e.key === 'Escape') {
                                thresholdCancelledRef.current = true;
                                e.currentTarget.blur();
                              }
                            }}
                            placeholder="inherit"
                            className={`w-20 px-2 py-0.5 text-xs ${fieldCls}`}
                            aria-label="Score threshold"
                          />
                        ) : (
                          <button
                            type="button"
                            className="text-xs text-muted-foreground hover:text-foreground"
                            onClick={() => startEditThreshold(t)}
                            title="Per-template match score threshold (empty = inherit feed/global)"
                          >
                            Threshold{t.scoreThreshold != null ? `: ${t.scoreThreshold}` : ''}
                          </button>
                        )}
                        <button
                          type="button"
                          className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 disabled:cursor-not-allowed"
                          onClick={() => {
                            setActionError(null);
                            setOptimizeId(optimizeId === t.id ? null : t.id);
                          }}
                          disabled={!t.sourceEpisodeId}
                          title={!t.sourceEpisodeId
                            ? 'This cue has no source episode to rescan'
                            : 'Try small trims of this window and keep the one that matches best'}
                        >
                          Optimize window
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
                </div>
                {optimizeId === t.id && (
                  <CueWindowOptimizePanel
                    slug={slug}
                    template={t}
                    onClose={() => setOptimizeId(null)}
                  />
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
          captureMaxIntroSeconds={captureMaxIntroSeconds}
          captureMaxOutroSeconds={captureMaxOutroSeconds}
        />
      )}

      {scanOpen && <CueScanModal slug={slug} onClose={() => setScanOpen(false)} />}

      {crossEpisodeScanOpen && (
        <CueCrossEpisodeScanModal
          slug={slug}
          captureMinSeconds={captureMinSeconds}
          captureMaxSeconds={captureMaxSeconds}
          captureMaxIntroSeconds={captureMaxIntroSeconds}
          captureMaxOutroSeconds={captureMaxOutroSeconds}
          onClose={() => setCrossEpisodeScanOpen(false)}
          onSaved={invalidate}
        />
      )}

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
  const queryClient = useQueryClient();
  const [picking, setPicking] = useState(true);
  const [selectedEpisode, setSelectedEpisode] = useState<Episode | null>(null);
  const [scoreOverride, setScoreOverride] = useState<string>('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CueScanResponse | null>(null);
  const [suggestion, setSuggestion] = useState<ThresholdSuggestResponse | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [applied, setApplied] = useState(false);
  const activeRef = useRef(true);
  useEffect(() => () => { activeRef.current = false; }, []);

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

  const runSuggest = async () => {
    if (!selectedEpisode) return;
    setSuggesting(true);
    setSuggestion(null);
    setApplied(false);
    try {
      for (let i = 0; i < 180; i++) {
        const res = await suggestCueThreshold(slug, selectedEpisode.id, i === 0);
        if (!activeRef.current) return;
        if (res.status === 'error') {
          setError(res.error || 'Threshold suggest failed');
          return;
        }
        if (res.status === 'ready') {
          setSuggestion(res);
          return;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      setError('Threshold suggest timed out after 3 minutes');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Suggest failed');
    } finally {
      setSuggesting(false);
    }
  };

  const applySuggested = async (value: number) => {
    setApplied(false);
    if (!window.confirm(
      `Set the per-feed cue match threshold to ${value.toFixed(2)} for this feed? ` +
      `The global setting will not change.`,
    )) return;
    try {
      await updateFeed(slug, { cueTemplateScoreOverride: value });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      setApplied(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not apply');
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
              max={CUE_SCORE_MAX}
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
              if (n !== undefined && (Number.isNaN(n) || n < 0 || n > CUE_SCORE_MAX)) {
                setError(`threshold must be between 0 and ${CUE_SCORE_MAX}`);
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
            onClick={() => { setPicking(true); setResult(null); setSelectedEpisode(null); setSuggestion(null); }}
          >
            Pick different episode
          </button>
          <button
            type="button"
            className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`}
            onClick={runSuggest}
            disabled={suggesting}
          >
            {suggesting ? 'Suggesting...' : 'Suggest threshold'}
          </button>
        </div>

        {error && <p className="text-sm text-destructive mb-3">{error}</p>}
        {running && <LoadingSpinner size="sm" className="my-3" />}

        {suggestion?.suggestion && (() => {
          const s = suggestion.suggestion;
          const canApply = s.confidence !== 'low'
            && s.effectFloorWarning !== 'signal-below-floor';
          return (
            <div className="mb-3 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-sm">
              {s.suggested != null ? (
                <p className="font-mono">
                  noise {s.noiseCeiling?.toFixed(3)} / signal {s.signalFloor?.toFixed(3)}
                  {' '}(gap {s.gapWidth?.toFixed(3)}) across {suggestion.sampleEpisodes ?? '--'} episode(s)
                  {' -> suggested '}<span className="font-semibold">{s.suggested.toFixed(2)}</span>
                  {suggestion.currentThreshold != null && (
                    <span className="text-muted-foreground">
                      {' '}(current {suggestion.currentThreshold.toFixed(2)})
                    </span>
                  )}
                </p>
              ) : (
                <p className="text-muted-foreground">{s.reason}</p>
              )}
              {s.effectFloorWarning === 'signal-below-floor' && (
                <p className="mt-1 text-amber-600 dark:text-amber-400">
                  The real cue scores below the {s.effectFloor?.toFixed(2)} floor, so lowering the
                  match score only surfaces it in diagnostics; it will not change cuts. Re-capture a
                  cleaner cue or enable voiceover attenuation.
                </p>
              )}
              {s.suggested != null && (
                <button
                  type="button"
                  className={`mt-2 px-3 py-1.5 rounded ${ghostBtn} text-sm disabled:opacity-50`}
                  onClick={() => applySuggested(s.suggested as number)}
                  disabled={!canApply}
                >
                  Apply to this feed
                </button>
              )}
              {applied && (
                <span className="ml-2 text-xs text-green-600 dark:text-green-400">Saved as feed override</span>
              )}
            </div>
          );
        })()}

        {result && (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Threshold {result.thresholdUsed.toFixed(2)}
              {result.thresholdSource === 'override' && ' (feed override)'}
              {result.thresholdSource === 'global' && ' (global default)'}
              {result.thresholdSource === 'request' && ' (this scan only)'}
              {' '}- scan {result.elapsedSeconds.toFixed(1)}s
            </p>
            <ul className="divide-y divide-border border border-border rounded">
              {result.templates.map((t) => {
                // Use per-template effective threshold when available (set when
                // a per-template override governs this template's matching).
                const tplThreshold = (t as { effThreshold?: number }).effThreshold ?? result.thresholdUsed;
                const passed = t.peakScore >= tplThreshold;
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

interface CueWindowOptimizePanelProps {
  slug: string;
  template: CueTemplate;
  onClose: () => void;
}

// Inline before/after panel for the window optimizer (D2b). Mounting claims or
// polls the background sweep (D1b claim/poll convention); Apply moves the
// window via the template PATCH, which re-extracts blobs server-side.
function CueWindowOptimizePanel({ slug, template, onClose }: CueWindowOptimizePanelProps) {
  const queryClient = useQueryClient();
  const [applyError, setApplyError] = useState<string | null>(null);

  const queryKey = ['cue-window-optimize', slug, template.id];
  const query = useQuery<CueWindowOptimizeResponse>({
    queryKey,
    queryFn: () => optimizeCueWindow(slug, template.id),
    staleTime: Infinity,
    refetchInterval: (q) =>
      q.state.data?.status === 'scanning' ? 3000 : false,
  });

  // Collapse keeps nothing: drop the cached proposal once the panel unmounts
  // (Discard, Apply, or toggling the row action). Removing after unmount also
  // avoids an observer refetch re-claiming a scan server-side.
  useEffect(() => {
    return () => {
      queryClient.removeQueries({ queryKey: ['cue-window-optimize', slug, template.id] });
    };
  }, [queryClient, slug, template.id]);

  const data = query.data;
  const { proposedStartS, proposedEndS, meanPeakScore, baselineMeanPeakScore, perEpisode, baselineWindow } = data ?? {};
  const scanning = query.isLoading || data?.status === 'scanning';
  // A thrown trigger error (e.g. 409 source original aged out) carries the
  // server message; a saved worker error comes back in the payload.
  const scanError = data?.status === 'error'
    ? (data.error || 'Optimize failed.')
    : query.error
      ? (query.error instanceof Error ? query.error.message : 'Optimize failed.')
      : null;
  const ready = data?.status === 'ready'
    && proposedStartS != null && proposedEndS != null && meanPeakScore != null;
  const alreadyOptimal = ready
    && proposedStartS === baselineWindow?.startS
    && proposedEndS === baselineWindow?.endS;
  const scoreDelta = ready && baselineMeanPeakScore != null
    ? meanPeakScore - baselineMeanPeakScore
    : null;

  const applyMutation = useMutation({
    mutationFn: (vars: { startS: number; endS: number }) =>
      updateCueTemplate(template.id, {
        sourceOffsetS: vars.startS,
        // Round away float dust from end - start; the sweep works in 0.1s steps.
        durationS: Math.round((vars.endS - vars.startS) * 1000) / 1000,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] });
      onClose();
    },
    onError: (e) => setApplyError(e instanceof Error ? e.message : 'Apply failed'),
  });

  // Force a fresh server-side run; same fetchQuery idiom as the cross-episode
  // modal's rescan.
  const rescan = () => {
    setApplyError(null);
    queryClient.fetchQuery({
      queryKey,
      queryFn: () => optimizeCueWindow(slug, template.id, true),
      staleTime: 0,
    });
  };

  return (
    <div className="mt-2 rounded border border-border bg-secondary/30 px-3 py-2 text-xs">
      {scanning && (
        <p className="text-muted-foreground flex items-center gap-2">
          <LoadingSpinner size="sm" inline /> Testing window trims across episodes, this can take a minute...
        </p>
      )}
      {!scanning && scanError && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-destructive">{scanError}</p>
          <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={rescan}>
            Rescan
          </button>
          <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={onClose}>
            Close
          </button>
        </div>
      )}
      {!scanning && !scanError && ready && (
        <div className="space-y-2">
          {alreadyOptimal ? (
            <p>
              <span className="px-2 py-0.5 rounded font-medium bg-green-500/20 text-green-600 dark:text-green-400">
                Already optimal
              </span>
              <span className="ml-2 text-muted-foreground">
                No trim scored higher than the current window.
              </span>
            </p>
          ) : (
            <div className="grid max-w-sm grid-cols-[auto_1fr_1fr] gap-x-4 gap-y-0.5 font-mono">
              <span />
              <span className="font-sans text-muted-foreground">Current</span>
              <span className="font-sans text-muted-foreground">Proposed</span>
              <span className="font-sans text-muted-foreground">Start</span>
              <span>{baselineWindow ? `${baselineWindow.startS.toFixed(2)}s` : '--'}</span>
              <span>{proposedStartS.toFixed(2)}s</span>
              <span className="font-sans text-muted-foreground">End</span>
              <span>{baselineWindow ? `${baselineWindow.endS.toFixed(2)}s` : '--'}</span>
              <span>{proposedEndS.toFixed(2)}s</span>
              <span className="font-sans text-muted-foreground">Score</span>
              <span title={baselineMeanPeakScore == null
                ? 'The current window is outside the capture bounds, so it was not scored'
                : undefined}
              >
                {baselineMeanPeakScore != null ? baselineMeanPeakScore.toFixed(3) : '--'}
              </span>
              <span>
                {meanPeakScore.toFixed(3)}
                {scoreDelta != null && (
                  <span className={`ml-1 ${scoreDelta >= 0
                    ? 'text-green-600 dark:text-green-400'
                    : 'text-amber-600 dark:text-amber-400'}`}
                  >
                    {scoreDelta >= 0 ? '+' : ''}{scoreDelta.toFixed(3)}
                  </span>
                )}
              </span>
            </div>
          )}
          {(perEpisode?.length ?? 0) > 0 && (
            <p className="text-muted-foreground">
              Per episode:{' '}
              {perEpisode!.map((e) => (
                <span key={e.episodeId} className="mr-2 font-mono">
                  {e.episodeId.slice(0, 8)} {e.peakScore.toFixed(3)}
                </span>
              ))}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={rescan}>
              Rescan
            </button>
            <button type="button" className={`px-2 py-1 rounded ${ghostBtn}`} onClick={onClose}>
              Discard
            </button>
            {!alreadyOptimal && (
              <button
                type="button"
                className={`px-2 py-1 rounded ${primaryBtn} disabled:opacity-50`}
                onClick={() => {
                  setApplyError(null);
                  applyMutation.mutate({ startS: proposedStartS, endS: proposedEndS });
                }}
                disabled={applyMutation.isPending}
              >
                {applyMutation.isPending ? 'Applying...' : 'Apply'}
              </button>
            )}
          </div>
          {applyError && <p className="text-destructive">{applyError}</p>}
        </div>
      )}
    </div>
  );
}

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

function CueCrossEpisodeScanModal({
  slug,
  captureMinSeconds,
  captureMaxSeconds,
  captureMaxIntroSeconds,
  captureMaxOutroSeconds,
  onClose,
  onSaved,
}: CueCrossEpisodeScanModalProps) {
  useEscape(onClose);
  const queryClient = useQueryClient();
  const [pickerPage, setPickerPage] = useState(0);
  // Selected episodes in click order (first = target). Full objects, not ids,
  // so title/duration survive paging away from the page they were picked on.
  const [selected, setSelected] = useState<Episode[]>([]);
  // Phase: picker -> results (scanning/ready/error handled in scanQuery state).
  const [phase, setPhase] = useState<'picker' | 'results'>('picker');
  // Seed for CueMarkModal when a candidate's "Make template" is clicked.
  const [seed, setSeed] = useState<CrossEpisodeCandidate | null>(null);

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
  const targetEp = selected[0];

  // React Query scan: enabled once user advances to results phase.
  // Polling stops when status is no longer 'scanning'.
  const scanQueryKey = ['cue-cross-episode-scan', slug, selectedIds];
  const scanQuery = useQuery<CrossEpisodeScanResponse>({
    queryKey: scanQueryKey,
    queryFn: () => crossEpisodeScan(slug, selectedIds),
    enabled: phase === 'results' && selected.length >= CROSS_EPISODE_MIN,
    staleTime: Infinity,
    refetchInterval: (q) =>
      q.state.data?.status === 'scanning' ? 3000 : false,
  });

  const scanData = scanQuery.data;
  const scanning = phase === 'results' && (scanQuery.isLoading || scanData?.status === 'scanning');
  const scanError = scanData?.status === 'error'
    ? (scanData.error || 'Scan failed.')
    : (scanQuery.error ? 'Scan failed. Try again.' : null);
  const candidates: CrossEpisodeCandidate[] = scanData?.candidates ?? [];

  const toggleEpisode = (ep: Episode) => {
    setSelected((prev) => {
      if (prev.some((p) => p.id === ep.id)) return prev.filter((p) => p.id !== ep.id);
      if (prev.length >= CROSS_EPISODE_MAX) return prev;
      return [...prev, ep];
    });
  };

  // Force a fresh server-side run; same fetchQuery idiom as
  // CueCandidatesSection's rescan, so repeated clicks always refetch.
  const rescan = () =>
    queryClient.fetchQuery({
      queryKey: scanQueryKey,
      queryFn: () => crossEpisodeScan(slug, selectedIds, true),
      staleTime: 0,
    });

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
                            {ep.published ? new Date(ep.published).toLocaleDateString() : 'unknown date'}
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
              onClick={() => setPhase('picker')}
            >
              Change episodes
            </button>
            {!scanning && (
              <button
                type="button"
                className={`px-3 py-1.5 rounded ${ghostBtn} text-sm`}
                onClick={() => rescan()}
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

          {candidates.length > 0 && (
            <ul className="flex-1 overflow-y-auto divide-y divide-border border border-border rounded">
              {candidates.map((c) => (
                <li key={`${c.start}-${c.end}`} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <div className="flex-1 min-w-0">
                    <span className="font-mono text-sm">
                      {formatTimestamp(c.start)} - {formatTimestamp(c.end)}
                    </span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      {(c.end - c.start).toFixed(2)}s
                    </span>
                    {c.episodeMatches != null && (
                      <span className="ml-2 px-1.5 py-0.5 text-xs rounded font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                        matched in {c.episodeMatches} ep{c.episodeMatches === 1 ? '' : 's'}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    className={`shrink-0 px-3 py-1.5 rounded ${primaryBtn} text-xs`}
                    onClick={() => setSeed(c)}
                  >
                    Make template
                  </button>
                </li>
              ))}
            </ul>
          )}
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

export default CueTemplatesPanel;
