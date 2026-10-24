import { Modal, dialogFooterBtn } from '../../components/Modal';
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Plus, Square } from 'lucide-react';
import CloseButton from '../../components/CloseButton';
import StatTile from '../../components/StatTile';
import CollapsibleSection, { useCollapsibleOpen } from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import CueMarkModal from '../../components/CueMarkModal';
import CueWindowOptimizePanel from '../../components/CueWindowOptimizePanel';
import CueCrossEpisodeScanModal from '../../components/CueCrossEpisodeScanModal';
import { ghostBtn, primaryBtn } from '../../components/cueScanStyles';
import { btnBase, btnDestructive, btnDestructiveOutline, btnGhost, btnOutline, btnPrimary } from '../../components/buttonStyles';
import { cardTitle, subHeading } from '../../components/pageStyles';
import { ConfirmModal } from '../../components/Modal';
import {
  CUE_TYPE_OPTIONS,
  cueTemplateAudioUrl,
  cueTemplateExportUrl,
  deleteCueTemplate,
  importCueTemplate,
  listCueTemplates,
  previewCueTemplate,
  scanEpisodeCues,
  suggestCueThreshold,
  updateCueTemplate,
  type CueScanResponse,
  type CueTemplate,
  type CueTemplateScope,
  type CueTemplateType,
  type ThresholdSuggestResponse,
} from '../../api/cueTemplates';
import { getCueFeedAdvisory } from '../../api/cueDetections';
import { getEpisode, getEpisodes, getFeed, getFeeds, updateFeed, CUE_SCORE_MAX } from '../../api/feeds';
import { getSettings } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import type { Episode } from '../../api/types';
import { formatTime } from '../../utils/adReviewHelpers';
import { formatDate, formatElapsed, formatPercent } from '../../utils/format';
import Checkbox from '../../components/Checkbox';
import TriStateSelect from '../../components/TriStateSelect';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import { focusRing, labelBase, selectBase } from '../../components/fieldStyles';
import { badgeBase, tint } from '../../components/badgeStyles';

const PICKER_PAGE_SIZE = 50;

interface Props {
  slug: string;
}

// Per-feed cue template management. Templates take precedence over the global
// spectral cue detector when at least one is enabled for the feed.

function CueTemplatesPanel({ slug }: Props) {
  const queryClient = useQueryClient();
  // Mirrors the CollapsibleSection's persisted open state (same storage key)
  // so the panel's queries only run once the section is actually visible.
  const [panelOpen, setPanelOpen] = useCollapsibleOpen(`feed-cue-templates-${slug}`);
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
    enabled: !!slug && panelOpen,
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
  const inheritedCutMatches =
    feedQuery.data?.cueCutMatchesOverride ?? settingsQuery.data?.audioCueCutMatches?.value;
  const inheritLabel = inheritedCutMatches == null
    ? 'Inherit'
    : `Inherit (${inheritedCutMatches ? 'on' : 'off'})`;
  const captureMinSeconds = settingsQuery.data?.audioCueCaptureMinSeconds?.value ?? 0.2;
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 10;
  const captureMaxIntroSeconds = settingsQuery.data?.audioCueCaptureMaxIntroSeconds?.value ?? 60;
  const captureMaxOutroSeconds = settingsQuery.data?.audioCueCaptureMaxOutroSeconds?.value ?? 60;

  // Per-feed cue health, so the user can judge a feed's cues before enabling
  // cue-pair synthesis (#350 follow-up). Empty until episodes are processed.
  const advisoryQuery = useQuery({
    queryKey: ['cue-advisory', slug],
    queryFn: () => getCueFeedAdvisory(slug),
    enabled: !!slug && panelOpen,
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
    enabled: !!slug && panelOpen,
    staleTime: 60_000,
  });
  const eligibleCount = (eligibleQuery.data?.episodes ?? []).filter(
    (ep) => ep.hasOriginalAudio !== false,
  ).length;
  const crossEpisodeEligible = !eligibleQuery.data || eligibleCount >= 2;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { cueType?: CueTemplateType; enabled?: boolean; removeWithAd?: boolean; cutMatches?: boolean | null; scope?: CueTemplateScope; networkId?: string; scoreThreshold?: number | null } }) =>
      updateCueTemplate(id, patch),
    onSuccess: invalidate,
    onError: (e) => setActionError(getErrorMessage(e, 'Could not update this cue.')),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteCueTemplate(id),
    onSuccess: invalidate,
    onError: (e) => setActionError(getErrorMessage(e, 'Could not delete this cue.')),
  });

  const templates: CueTemplate[] = templatesQuery.data ?? [];

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
      template.scoreThreshold != null ? String(Math.round(template.scoreThreshold * 100)) : '',
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
    const percent = Number(trimmed);
    if (!Number.isInteger(percent) || percent < 30 || percent > 99) {
      setActionError('Match threshold must be a whole number between 30% and 99%.');
      return;
    }
    const val = percent / 100;
    const current = template.scoreThreshold == null ? null : Math.round(template.scoreThreshold * 100);
    if (percent !== current) {
      updateMutation.mutate({ id: template.id, patch: { scoreThreshold: val } });
    }
  };

  const handlePromote = async (template: CueTemplate) => {
    setActionError(null);
    if (template.scope === 'network') {
      if (networkId && template.networkId !== networkId) {
        // The owning feed's network moved; follow it instead of demoting.
        updateMutation.mutate({ id: template.id, patch: { scope: 'network', networkId } });
        return;
      }
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
      setActionError(getErrorMessage(e, 'Could not load network feeds'));
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
      setActionError(getErrorMessage(e, 'Could not check recent episodes.'));
    } finally {
      setVerifying(false);
    }
  };

  const importMutation = useMutation({
    mutationFn: (file: File) => importCueTemplate(slug, file),
    onSuccess: invalidate,
    onError: (e) => setActionError(getErrorMessage(e, 'Could not import this cue.')),
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
      setActionError(getErrorMessage(e, 'Could not open this episode'));
    }
  };

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Audio cue templates"
        subtitle="A recurring ding or stinger the matcher snaps ad edges to."
        defaultOpen={false}
        storageKey={`feed-cue-templates-${slug}`}
        onToggle={setPanelOpen}
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
            className={ghostBtn}
            onClick={() => importInputRef.current?.click()}
            disabled={importMutation.isPending}
            title="Import a cue template zip exported from another install"
          >
            {importMutation.isPending ? 'Importing...' : 'Import'}
          </button>
          <button
            type="button"
            className={ghostBtn}
            onClick={() => setScanOpen(true)}
            disabled={templates.length === 0}
            title={templates.length === 0 ? 'Mark at least one cue first' : 'Run all enabled templates against an episode'}
          >
            Test on episode
          </button>
          <button
            type="button"
            className={ghostBtn}
            onClick={() => { setActionError(null); setCrossEpisodeScanOpen(true); }}
            disabled={!crossEpisodeEligible}
            title={!crossEpisodeEligible ? 'Need at least 2 episodes with original audio' : 'Find recurring cue sounds across multiple episodes'}
          >
            Find across episodes
          </button>
          <button
            type="button"
            className={primaryBtn}
            onClick={() => { setActionError(null); setPickerOpen(true); }}
          >
            <Plus className="w-4 h-4" aria-hidden="true" />
            Mark cue
          </button>
        </div>

        {actionError && <p className="text-sm text-destructive mb-2">{actionError}</p>}
        {verifying && (
          <p className="text-sm text-muted-foreground mb-2 flex items-center gap-2">
            <LoadingSpinner size="sm" inline /> Checking recent episodes...
          </p>
        )}
        {verifyState && !verifying && (
          <p className={`text-sm mb-2 ${verifyState.matched > 0 ? 'text-success' : 'text-warning'}`}>
            Cue "{verifyState.label}" matched {verifyState.matched} of {verifyState.checked} recent
            episode{verifyState.checked === 1 ? '' : 's'}.
            {verifyState.matched === 0 ? ' No matches yet - it may not recur, or the bracket is loose.' : ''}
          </p>
        )}
        {panelOpen && templatesQuery.isLoading && <LoadingSpinner size="sm" className="my-2" />}
        {panelOpen && templatesQuery.error && (
          <p className="text-sm text-destructive">Could not load cue templates.</p>
        )}

        {/* Gated on panelOpen too: while collapsed the query is disabled
            (isLoading reads false), so without this the empty state would
            render even though no fetch has actually run. */}
        {panelOpen && !templatesQuery.isLoading && templates.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No cues yet. Mark one to start.
          </p>
        )}

        {templates.length > 0 && (
          <ul className="divide-y divide-border border-t border-border">
            {templates.map((t) => (
              <li key={t.id} className="px-3 py-2 text-sm">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <Checkbox
                    className="min-h-11 min-w-11 justify-center"
                    checked={t.enabled}
                    onChange={() => handleToggle(t)}
                    disabled={t.owned === false}
                    title={t.owned === false ? 'Managed on the feed that created it' : undefined}
                    ariaLabel={`Enable cue ${t.label}`}
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
                      className={`w-full ${selectBase}`}
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
                          <span className={`${badgeBase} ml-2 ${tint.purple} align-middle`}>
                            Network
                          </span>
                        )}
                        {t.quiet && (
                          <span
                            className={`${badgeBase} ml-2 ${tint.warning} align-middle`}
                            title="No matches in recent episodes. Recapture this cue to keep it working."
                          >
                            Quiet
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t.durationS.toFixed(2)}s - marked at {formatTime(t.sourceOffsetS)}
                        {t.sourceEpisodeId ? ` of episode ${t.sourceEpisodeId.slice(0, 8)}` : ''}
                        {t.lastMatchAt ? ` - last match ${formatDate(t.lastMatchAt)}` : ''}
                      </p>
                      {t.cueType.startsWith('ad_break_') && (
                        <Checkbox
                          className="min-h-11"
                          label="Remove cue with ad"
                          labelClassName="text-xs text-muted-foreground"
                          checked={t.removeWithAd ?? true}
                          onChange={(removeWithAd) => updateMutation.mutate({ id: t.id, patch: { removeWithAd } })}
                          disabled={t.owned === false || updateMutation.isPending}
                          ariaLabel={`Remove cue with ad: ${t.label}`}
                        />
                      )}
                      {t.cueType !== 'content_transition' && (
                        <div className="flex flex-col gap-1">
                          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
                            <label htmlFor={`cut-matches-${t.id}`} className={`${labelBase} mb-0`}>
                              Cut matched audio
                            </label>
                            <TriStateSelect
                              id={`cut-matches-${t.id}`}
                              ariaDescribedBy={`cut-matches-help-${t.id}`}
                              value={t.cutMatches ?? null}
                              onChange={(cutMatches) => updateMutation.mutate({ id: t.id, patch: { cutMatches } })}
                              disabled={t.owned === false || updateMutation.isPending}
                              globalLabel={inheritLabel}
                              enableLabel="On"
                              disableLabel="Off"
                              className={`${selectBase} w-full sm:w-auto`}
                            />
                          </div>
                          <p id={`cut-matches-help-${t.id}`} className="text-xs text-muted-foreground">
                            Cuts every match of this cue on its own. The feed's category action for the cue type decides remove, keep or mark.
                          </p>
                        </div>
                      )}
                      {t.owned !== false && t.scope === 'network' && networkId
                        && t.networkId && t.networkId !== networkId && (
                        <p className="text-xs text-warning">
                          Shared on network "{t.networkId}", this feed is on "{networkId}"
                        </p>
                      )}
                    </>
                  )}
                  </div>
                </div>
                {editingId !== t.id && (
                  <div className="flex flex-wrap items-center gap-2 pl-7 sm:pl-0 sm:shrink-0">
                    {t.hasAudio !== false && (
                      <button
                        type="button"
                        className={`${btnBase} ${btnGhost}`}
                        onClick={() => togglePlay(t)}
                        title={playingId === t.id ? 'Stop' : 'Play this cue'}
                        aria-label={playingId === t.id ? `Stop cue ${t.label}` : `Play cue ${t.label}`}
                      >
                        {playingId === t.id ? <Square className="w-4 h-4" aria-hidden="true" /> : <Play className="w-4 h-4" aria-hidden="true" />}
                        {playingId === t.id ? 'Stop' : 'Play'}
                      </button>
                    )}
                    <a
                      className={`${btnBase} ${btnGhost}`}
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
                        {(t.scope === 'network' || networkId) && (() => {
                          const mismatched = t.scope === 'network' && !!networkId
                            && t.networkId !== networkId;
                          return (
                            <button
                              type="button"
                              className={`${btnBase} ${btnGhost}`}
                              onClick={() => handlePromote(t)}
                              title={
                                t.scope === 'network'
                                  ? (mismatched
                                      ? `Move this cue to network "${networkId}"`
                                      : 'Limit this cue to this feed only')
                                  : `Apply this cue to every feed on network "${networkId}"`
                              }
                            >
                              {t.scope === 'network'
                                ? (mismatched ? `Move to network ${networkId}` : 'Make podcast-only')
                                : 'Promote to network'}
                            </button>
                          );
                        })()}
                        <button
                          type="button"
                          className={`${btnBase} ${btnGhost}`}
                          onClick={() => startEditType(t)}
                        >
                          Change type
                        </button>
                        {editingThresholdId === t.id ? (
                          <DraftNumberInput
                            autoFocus
                            min={30}
                            max={99}
                            step={1}
                            ariaLabel={`Match threshold for ${t.label}`}
                            value={parseOptionalNumber(editThresholdValue)}
                            fallback={null}
                            parse={parseOptionalNumber}
                            onChange={(v) => setEditThresholdValue(v === null ? '' : String(v))}
                            onBlur={() => commitThreshold(t)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') e.currentTarget.blur();
                              if (e.key === 'Escape') {
                                thresholdCancelledRef.current = true;
                                e.currentTarget.blur();
                              }
                            }}
                            placeholder="inherit"
                            className="w-20"
                            aria-label="Score threshold"
                          />
                        ) : (
                          <button
                            type="button"
                            className={`${btnBase} ${btnGhost}`}
                            onClick={() => startEditThreshold(t)}
                            title="Per-template match score threshold (empty = inherit feed/global)"
                          >
                            Threshold{t.scoreThreshold != null ? `: ${formatPercent(t.scoreThreshold)}` : ''}
                          </button>
                        )}
                        <button
                          type="button"
                          className={`${btnBase} ${btnGhost}`}
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
                              className={`${btnBase} ${btnDestructive}`}
                              onClick={() => { deleteMutation.mutate(t.id); setConfirmDeleteId(null); }}
                            >
                              Delete cue
                            </button>
                            <button
                              type="button"
                              className={`${btnBase} ${btnGhost}`}
                              onClick={() => setConfirmDeleteId(null)}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className={`${btnBase} ${btnDestructiveOutline}`}
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
            <h4 className={`${subHeading} mb-1`}>Cue health</h4>
            <p className="text-xs text-muted-foreground mb-3">
              How this feed's cues have done across processed episodes. Use it to
              decide whether to turn on Create ads from cue pairs.
            </p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {([
                ['Detections', String(advisoryQuery.data.total)],
                ['Paired / snapped', `${advisoryQuery.data.paired} / ${advisoryQuery.data.snapped}`],
                ['Confirm rate', advisoryQuery.data.confirmRate != null
                  ? formatPercent(advisoryQuery.data.confirmRate) : '--'],
                ['Match range', advisoryQuery.data.minScore != null
                  ? `${formatPercent(advisoryQuery.data.minScore)} to ${formatPercent(advisoryQuery.data.maxScore ?? 0)}`
                  : '--'],
              ] as const).map(([label, value]) => (
                <StatTile key={label} label={label} value={value} />
              ))}
            </div>
            {(advisoryQuery.data.templateHints?.length ?? 0) > 0 && (
              <div className="mt-3 space-y-1">
                {advisoryQuery.data.templateHints!.map((h) => (
                  <p key={h.templateId} className="text-sm text-muted-foreground">
                    <span className={`${badgeBase} ${tint.warning} mr-2`}>
                      {h.hint === 'raise_threshold' ? 'Raise threshold' : 'Recapture cue'}
                    </span>
                    {h.label || `Template ${h.templateId}`}: {h.rejected} rejected
                    {h.confirmed > 0 && `, ${h.confirmed} confirmed`}
                    {h.hint === 'raise_threshold'
                      ? '. Rejections sit just above the current threshold.'
                      : '. Rejections span the score range; this capture matches the wrong audio.'}
                  </p>
                ))}
              </div>
            )}
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
        <Modal
          onClose={() => setPromoteState(null)}
          ariaLabel="Promote cue to network"
          panelClassName="text-foreground w-full max-w-md"
          header={(
            <h3 className={cardTitle}>Promote to network</h3>
          )}
          footer={(
            <>
              <button type="button" className={`${dialogFooterBtn} ${btnOutline}`} onClick={() => setPromoteState(null)}>
                Cancel
              </button>
              <button type="button" className={`${dialogFooterBtn} ${btnPrimary}`} onClick={confirmPromote}>
                Promote
              </button>
            </>
          )}
        >
          <div className="p-4">
            <p className="text-sm text-muted-foreground mb-3">
              Cue "{promoteState.template.label}" will start matching every feed on
              network "{networkId}" ({promoteState.feeds.length}):
            </p>
            <ul className="text-sm border border-border rounded divide-y divide-border max-h-48 overflow-y-auto">
              {promoteState.feeds.map((f) => (
                <li key={f.slug} className="px-3 py-1.5 truncate">{f.title || f.slug}</li>
              ))}
            </ul>
          </div>
        </Modal>
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
    <Modal
      onClose={onClose}
      ariaLabel="Pick an episode"
      panelClassName="text-foreground w-full max-w-2xl max-h-[85vh]"
      header={(
        <>
          <div>
            <h3 className={cardTitle}>Pick an episode</h3>
            <p className="text-xs text-muted-foreground">
              Any episode with retained original audio. A cue applies to the whole feed.
            </p>
          </div>
          <CloseButton onClick={onClose} />
        </>
      )}
      footer={totalPages > 1 ? (
        <div className="flex items-center justify-between w-full text-sm">
          <button
            type="button"
            className={ghostBtn}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            Prev
          </button>
          <span className="text-muted-foreground">
            Page {page + 1} of {totalPages} ({total} episodes)
          </span>
          <button
            type="button"
            className={ghostBtn}
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page + 1 >= totalPages}
          >
            Next
          </button>
        </div>
      ) : undefined}
    >
        <div>
          {query.isLoading && <div className="p-4"><LoadingSpinner size="sm" /></div>}
          {query.error && <p className="p-3 text-sm text-destructive">Could not load episodes.</p>}
          {!query.isLoading && episodes.length === 0 && (
            <p className="p-3 text-sm text-muted-foreground">No episodes match these filters.</p>
          )}
          {episodes.length > 0 && (
            <ul className="divide-y divide-border">
              {episodes.map((ep) => (
                <li key={ep.id}>
                  <button
                    type="button"
                    onClick={() => onPick(ep)}
                    className={`w-full text-left px-3 py-2 hover:bg-muted/50 ${focusRing}`}
                  >
                    <p className="text-sm font-medium truncate">{ep.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {ep.published ? formatDate(ep.published) : '-'}
                      {' - '}{ep.status}
                      {typeof ep.duration === 'number' && ep.duration > 0 ? ` - ${formatElapsed(ep.duration)}` : ''}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
    </Modal>
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
  const [pendingThreshold, setPendingThreshold] = useState<number | null>(null);
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
      setError(getErrorMessage(e, 'Could not scan this episode.'));
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
          setError(res.error || 'Could not suggest a threshold.');
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
      setError(getErrorMessage(e, 'Could not suggest a threshold.'));
    } finally {
      setSuggesting(false);
    }
  };

  const applySuggested = async (value: number) => {
    setPendingThreshold(value);
  };

  const doApplySuggested = async (value: number) => {
    setPendingThreshold(null);
    setApplied(false);
    try {
      await updateFeed(slug, { cueTemplateScoreOverride: value });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      setApplied(true);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not apply the threshold.'));
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
      setError(getErrorMessage(e, 'Could not load this episode.'));
    }
  };

  if (picking) {
    return <EpisodePicker slug={slug} onClose={onClose} onPick={onPick} />;
  }

  return (
    <Modal
      onClose={onClose}
      ariaLabel="Cue scan"
      panelClassName="text-foreground w-full max-w-3xl max-h-[90vh]"
      header={(
        <>
          <div className="min-w-0">
            <h3 className={cardTitle}>Cue scan</h3>
            <p className="text-xs text-muted-foreground truncate max-w-xl">{selectedEpisode?.title}</p>
          </div>
          <CloseButton onClick={onClose} />
        </>
      )}
    >
      <div className="p-4">
        <div className="flex flex-wrap items-end gap-3 mb-4">
          <div>
            <label className={labelBase} htmlFor="score-override">
              Score threshold (optional)
            </label>
            <DraftNumberInput
              id="score-override"
              min={0}
              max={CUE_SCORE_MAX}
              step={0.05}
              placeholder="default"
              value={parseOptionalNumber(scoreOverride)}
              fallback={null}
              parse={parseOptionalNumber}
              onChange={(v) => setScoreOverride(v === null ? '' : String(v))}
              className="w-28 font-mono"
            />
          </div>
          <button
            type="button"
            className={ghostBtn}
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
            className={ghostBtn}
            onClick={() => { setPicking(true); setResult(null); setSelectedEpisode(null); setSuggestion(null); }}
          >
            Pick a different episode
          </button>
          <button
            type="button"
            className={ghostBtn}
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
            <div className="mb-3 rounded-lg bg-muted/40 px-3 py-2 text-sm">
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
                <p className="mt-1 text-warning">
                  The real cue scores below the {s.effectFloor?.toFixed(2)} floor, so lowering the
                  match score only surfaces it in diagnostics; it will not change cuts. Re-capture a
                  cleaner cue or enable voiceover attenuation.
                </p>
              )}
              {s.suggested != null && (
                <button
                  type="button"
                  className={`mt-2 ${ghostBtn}`}
                  onClick={() => applySuggested(s.suggested as number)}
                  disabled={!canApply}
                >
                  Apply to this feed
                </button>
              )}
              {applied && (
                <span className="ml-2 text-xs text-success">Saved as feed override</span>
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
            <ul className="divide-y divide-border border-t border-border">
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
                        <p className={`text-sm font-mono ${passed ? 'text-success' : 'text-warning'}`}>
                          Peak {formatPercent(t.peakScore)} match
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
                            {formatTime(m.start)} - {formatPercent(m.score)} match
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
      {pendingThreshold !== null && (
        <ConfirmModal
          title="Apply the suggested threshold?"
          confirmLabel="Apply threshold"
          busyLabel="Applying..."
          destructive={false}
          onCancel={() => setPendingThreshold(null)}
          onConfirm={() => doApplySuggested(pendingThreshold)}
        >
          <p>Sets this feed's cue match threshold to {pendingThreshold.toFixed(2)}. The global setting does not change.</p>
        </ConfirmModal>
      )}
      </div>
    </Modal>
  );
}

export default CueTemplatesPanel;
