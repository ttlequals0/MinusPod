import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getNetworks, updateFeed, UpdateFeedPayload, CUE_SCORE_MIN, CUE_SCORE_MAX, rerenderSegments, RerenderSegmentsResult } from '../../api/feeds';
import { listCueTemplates } from '../../api/cueTemplates';
import { getSettings } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import CollapsibleSection, { useCollapsibleOpen } from '../../components/CollapsibleSection';
import CopyButton from '../../components/CopyButton';
import { ExperimentalBadge } from '../../components/ExperimentalBadge';
import { FeedTagsEditor } from '../../components/FeedTagsEditor';
import ToggleSwitch from '../../components/ToggleSwitch';
import TriStateSelect from '../../components/TriStateSelect';
import SegmentActionToggle from '../../components/SegmentActionToggle';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, SEGMENT_CATEGORY_DESCRIPTIONS, DEFAULT_SEGMENT_ACTION,
  type SegmentCategory, type SegmentAction,
} from '../../utils/segmentCategory';
import { WHISPER_LANGUAGES, labelForLanguage } from '../../utils/whisperLanguages';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { btnPrimary, btnSecondary, btnOutline } from '../../components/buttonStyles';

interface Props {
  feed: Feed;
  slug: string;
}

// Hint shown below the processing-mode select, keyed by the resolved mode
// (feed.processingMode ?? 'standard').
const PROCESSING_MODE_HINTS: Record<NonNullable<Feed['processingMode']>, { text: string; className: string }> = {
  standard: {
    text: 'Detects ads with the model and cuts them out. The default for most feeds.',
    className: 'text-xs text-muted-foreground',
  },
  keep_content: {
    text: 'Removes everything the model does not mark as show content. For feeds with '
      + 'unrecognizable inserted ads. Safety checks revert to normal removal when the '
      + 'labeling looks off, but they can miss a single mislabeled stretch and cut real '
      + 'audio. Check each episode.',
    className: 'text-xs text-warning',
  },
  skip_detection: {
    text: 'Episodes are still transcribed and get chapters and a transcript, but nothing '
      + 'is scanned for ads and nothing is cut. For ad-free shows; skips the ad '
      + 'detection cost.',
    className: 'text-xs text-muted-foreground',
  },
  passthrough: {
    text: 'Episodes are downloaded and served exactly as published: no transcription, ad '
      + 'detection, or cutting. The feed URL stays the same, so switching to another '
      + 'mode resumes processing for new episodes. Episodes already served untouched '
      + 'keep their original audio until you reprocess them.',
    className: 'text-xs text-warning',
  },
  cue_only: {
    text: 'Cuts come only from this feed\'s ad-break start and end cue templates: no '
      + 'model call, no verification pass, and no LLM redetection. Needs one enabled '
      + 'ad-break start template and one enabled ad-break end template.',
    className: 'text-xs text-warning',
  },
};

// Props for the per-field cue override row. Defined at module scope so its
// identity is stable across parent renders (avoids remount on every keystroke).
interface CueOverrideRowProps {
  label: string;
  min: number;
  max: number;
  step: number;
  value: string;
  setValue: (v: string) => void;
  feedValue: number | null | undefined;
  hint: string;
  onBlur: () => void;
  disabled: boolean;
  placeholder?: string;
  description?: string;
  formatOverride?: (v: number) => string;
}

function CueOverrideRow({
  label, min, max, step, value, setValue, feedValue, hint, onBlur, disabled,
  placeholder = 'global', description, formatOverride = String,
}: CueOverrideRowProps) {
  const inputRow = (
    <div className="flex items-center gap-2 flex-wrap">
      <input
        type="number" min={min} max={max} step={step}
        value={value} placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onBlur={onBlur}
        disabled={disabled}
        className="w-24 px-2 py-1.5 text-sm bg-secondary border border-border rounded disabled:opacity-50"
      />
      <span className="text-xs text-muted-foreground">{hint}</span>
      {feedValue != null && (
        <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
          Override: {formatOverride(feedValue)}
        </span>
      )}
    </div>
  );
  return (
    <div className={`flex flex-col sm:flex-row ${description ? 'sm:items-start' : 'sm:items-center'} gap-2 sm:gap-3 text-sm`}>
      <span className={`text-muted-foreground whitespace-nowrap sm:w-32 shrink-0${description ? ' sm:pt-1.5' : ''}`}>{label}:</span>
      {description ? (
        <div className="flex flex-col gap-1 flex-1 min-w-0">
          {inputRow}
          <p className="text-xs text-warning">{description}</p>
        </div>
      ) : inputRow}
    </div>
  );
}

function FeedSettingsPanel({ feed, slug }: Props) {
  const queryClient = useQueryClient();
  // Mirrors the CollapsibleSection's persisted open state (same storage key)
  // so the networks list is only fetched once the panel is actually visible.
  const [panelOpen, setPanelOpen] = useCollapsibleOpen(`feed-settings-${slug}`);
  const [isEditingNetwork, setIsEditingNetwork] = useState(false);
  const [editNetworkOverride, setEditNetworkOverride] = useState<string>('');
  const [customNetwork, setCustomNetwork] = useState(false);
  const [editDaiPlatform, setEditDaiPlatform] = useState('');
  const [editAutoProcessOverride, setEditAutoProcessOverride] = useState<string>('global');
  const [editMaxEpisodes, setEditMaxEpisodes] = useState<string>('');
  const [isEditingSourceUrl, setIsEditingSourceUrl] = useState(false);
  const [editSourceUrl, setEditSourceUrl] = useState('');
  const [sourceUrlError, setSourceUrlError] = useState<string | null>(null);
  const [rerenderResult, setRerenderResult] = useState<RerenderSegmentsResult | null>(null);
  const [rerenderError, setRerenderError] = useState<string | null>(null);
  const [segmentActionError, setSegmentActionError] = useState<string | null>(null);
  const [addingTitleSkipPattern, setAddingTitleSkipPattern] = useState(false);
  const [titleSkipPatternInput, setTitleSkipPatternInput] = useState('');
  // Local source of truth for the per-feed override map, not the `feed`
  // prop: the PATCH replaces the stored map outright with no server merge,
  // so building from a stale prop between edits would drop the earlier one.
  const [segmentOverrides, setSegmentOverrides] =
    useState<Partial<Record<SegmentCategory, SegmentAction>>>(feed.segmentCategoryActions ?? {});

  const { data: networks } = useQuery({
    queryKey: ['networks'],
    queryFn: getNetworks,
    enabled: panelOpen,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  // Cue-only mode needs at least one enabled ad-break-start and ad-break-end
  // template; fetched here to gray out the option before the user picks it.
  const { data: cueTemplates } = useQuery({
    queryKey: ['cueTemplates', slug],
    queryFn: () => listCueTemplates(slug),
    enabled: panelOpen,
  });
  const hasEnabledCueTemplate = (cueType: 'ad_break_start' | 'ad_break_end') =>
    (cueTemplates ?? []).some((t) => t.cueType === cueType && t.enabled);
  const cueOnlyEligible = hasEnabledCueTemplate('ad_break_start') && hasEnabledCueTemplate('ad_break_end');

  const s = (v: number | null | undefined) => (v != null ? String(v) : '');

  // Per-field input state. useSyncFromQuery reseeds from the server value
  // whenever the query object identity changes (same render-phase pattern
  // used by Settings.tsx), avoiding the one-frame stale UI of useEffect.
  const [cueScoreInput, setCueScoreInput] = useState(
    feed.cueTemplateScoreOverride != null ? String(feed.cueTemplateScoreOverride) : '');
  const [pairMinInput, setPairMinInput] = useState(s(feed.cuePairMinBreakOverride));
  const [pairMaxInput, setPairMaxInput] = useState(s(feed.cuePairMaxBreakOverride));
  const [pairFracInput, setPairFracInput] = useState(s(feed.cuePairMaxBreakFractionOverride));
  const [snapConfInput, setSnapConfInput] = useState(s(feed.cueSnapConfidenceOverride));
  const [snapLeadInput, setSnapLeadInput] = useState(s(feed.cueSnapLeadOverride));
  const [snapLagInput, setSnapLagInput] = useState(s(feed.cueSnapLagOverride));
  const [maxAdDurInput, setMaxAdDurInput] = useState(s(feed.maxAdDurationOverride));
  const [maxAdDurRejectInput, setMaxAdDurRejectInput] = useState(s(feed.maxAdDurationRejectOverride));

  // Reseed inputs from the server feed object when it changes (e.g. after a
  // successful mutation or a background refetch). This mirrors useSyncFromQuery
  // applied to each field individually so that a mutation response immediately
  // reflects the persisted value without waiting for a second refetch.
  useSyncFromQuery(feed, (f) => {
    setCueScoreInput(f.cueTemplateScoreOverride != null ? String(f.cueTemplateScoreOverride) : '');
    setPairMinInput(s(f.cuePairMinBreakOverride));
    setPairMaxInput(s(f.cuePairMaxBreakOverride));
    setPairFracInput(s(f.cuePairMaxBreakFractionOverride));
    setSnapConfInput(s(f.cueSnapConfidenceOverride));
    setSnapLeadInput(s(f.cueSnapLeadOverride));
    setSnapLagInput(s(f.cueSnapLagOverride));
    setMaxAdDurInput(s(f.maxAdDurationOverride));
    setMaxAdDurRejectInput(s(f.maxAdDurationRejectOverride));
    setSegmentOverrides(f.segmentCategoryActions ?? {});
  });

  const updateMutation = useMutation({
    mutationFn: (data: UpdateFeedPayload) => updateFeed(slug, data),
    onSuccess: (_data, variables) => {
      // Surface a newly-typed custom network in every other feed's dropdown.
      queryClient.invalidateQueries({ queryKey: ['networks'] });
      setIsEditingNetwork(false);
      if (variables && 'segmentCategoryActions' in variables) {
        setSegmentActionError(null);
      }
    },
    // Rollback lives in each mutate() call's own onError below (react-query
    // v5 runs per-call callbacks alongside these), so it can restore the
    // exact pre-edit snapshot rather than the possibly-stale feed prop.
    // onSettled always refetches so a failed PATCH still reverts every
    // other field to server truth.
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
  });

  // Separate mutation from updateMutation: that one closes the network editor
  // on success and surfaces no error message, while a rejected source URL
  // (backend validates by fetching the feed) must stay in edit mode with the
  // backend's reason shown inline.
  const sourceUrlMutation = useMutation({
    mutationFn: (url: string) => updateFeed(slug, { sourceUrl: url }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      setIsEditingSourceUrl(false);
      setSourceUrlError(null);
    },
    onError: (e: Error) => setSourceUrlError(e.message),
  });

  // Full-map PATCH: each edit must send the whole override map, built from
  // segmentOverrides (see above), not the feed prop. Clearing the last
  // override sends null, not {}, so the feed comes back with no overrides
  // at all. `prev` snapshots the pre-edit state for rollback in onError.
  const setSegmentActionOverride = (category: SegmentCategory, action: SegmentAction) => {
    const prev = segmentOverrides;
    const next = { ...segmentOverrides, [category]: action };
    setSegmentOverrides(next);
    setSegmentActionError(null);
    updateMutation.mutate({ segmentCategoryActions: next }, {
      onError: (e) => {
        setSegmentOverrides(prev);
        setSegmentActionError(getErrorMessage(e, 'Failed to update segment action'));
      },
    });
  };

  const clearSegmentActionOverride = (category: SegmentCategory) => {
    const prev = segmentOverrides;
    const next = { ...segmentOverrides };
    delete next[category];
    setSegmentOverrides(next);
    setSegmentActionError(null);
    updateMutation.mutate({
      segmentCategoryActions: Object.keys(next).length > 0 ? next : null,
    }, {
      onError: (e) => {
        setSegmentOverrides(prev);
        setSegmentActionError(getErrorMessage(e, 'Failed to update segment action'));
      },
    });
  };

  const rerenderMutation = useMutation({
    mutationFn: () => rerenderSegments(slug),
    onSuccess: (result) => {
      setRerenderResult(result);
      setRerenderError(null);
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
    onError: (e: unknown) => {
      setRerenderResult(null);
      setRerenderError(getErrorMessage(e, 'Re-render failed'));
    },
  });

  const handleRerenderClick = () => {
    if (!window.confirm(
      'Re-render all processed episodes of this feed using the current segment actions? Episodes without a retained original are skipped.',
    )) return;
    rerenderMutation.mutate();
  };

  const startEditingSourceUrl = () => {
    setEditSourceUrl(feed.sourceUrl);
    setSourceUrlError(null);
    setIsEditingSourceUrl(true);
  };

  const saveSourceUrl = () => {
    const url = editSourceUrl.trim();
    if (!url) {
      setSourceUrlError('Source URL cannot be empty');
      return;
    }
    if (url === feed.sourceUrl) {
      setIsEditingSourceUrl(false);
      setSourceUrlError(null);
      return;
    }
    sourceUrlMutation.mutate(url);
  };

  function commitFloat(
    raw: string,
    serverValue: number | null | undefined,
    field: keyof UpdateFeedPayload,
    lo: number,
    hi: number,
    reset: () => void,
  ) {
    const trimmed = raw.trim();
    if (trimmed === '') {
      // Clearing the field: only mutate if there was actually a server value.
      if (serverValue != null) {
        updateMutation.mutate({ [field]: null });
      }
      return;
    }
    const v = parseFloat(trimmed);
    if (!Number.isNaN(v) && v >= lo && v <= hi) {
      // Only send the PATCH when the normalized value differs from the server value.
      if (v !== serverValue) {
        updateMutation.mutate({ [field]: v });
      }
    } else {
      reset();
    }
  }

  const startEditingNetwork = () => {
    const override = feed.networkIdOverride || '';
    // Until the network list loads we cannot tell a known network from a custom
    // one, so default to the dropdown (a fallback option renders the value)
    // rather than misreading a known network as custom.
    const networksLoaded = networks !== undefined;
    const isKnown = (networks ?? []).some((n) => n.id === override);
    setEditNetworkOverride(override);
    setCustomNetwork(networksLoaded && override !== '' && !isKnown);
    setEditDaiPlatform(feed.daiPlatform || '');
    if (feed.autoProcessOverride === true) {
      setEditAutoProcessOverride('enable');
    } else if (feed.autoProcessOverride === false) {
      setEditAutoProcessOverride('disable');
    } else {
      setEditAutoProcessOverride('global');
    }
    setEditMaxEpisodes(feed.maxEpisodes ? String(feed.maxEpisodes) : '');
    setIsEditingNetwork(true);
  };

  const saveNetworkEdit = () => {
    let autoProcessOverride: boolean | null = null;
    if (editAutoProcessOverride === 'enable') {
      autoProcessOverride = true;
    } else if (editAutoProcessOverride === 'disable') {
      autoProcessOverride = false;
    }

    const maxEp = editMaxEpisodes ? parseInt(editMaxEpisodes, 10) : null;

    updateMutation.mutate({
      networkIdOverride: editNetworkOverride.trim() || null,
      daiPlatform: editDaiPlatform || undefined,
      autoProcessOverride: autoProcessOverride,
      maxEpisodes: maxEp !== null && !isNaN(maxEp) ? Math.max(10, Math.min(maxEp, 500)) : null,
    });
  };

  const addTitleSkipPattern = () => {
    const pattern = titleSkipPatternInput.trim();
    if (!pattern) return;
    const current = feed.titleSkipPatterns ?? [];
    if (!current.includes(pattern)) {
      updateMutation.mutate({ titleSkipPatterns: [...current, pattern] });
    }
    setTitleSkipPatternInput('');
    setAddingTitleSkipPattern(false);
  };

  const removeTitleSkipPattern = (pattern: string) => {
    updateMutation.mutate({
      titleSkipPatterns: (feed.titleSkipPatterns ?? []).filter((p) => p !== pattern),
    });
  };

  const processingMode = feed.processingMode ?? 'standard';
  const cueOnlyActive = processingMode === 'cue_only';

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Feed settings"
        subtitle="Network, DAI platform, auto-processing, language, tags, and collapsed cue tuning and advanced controls"
        defaultOpen={false}
        storageKey={`feed-settings-${slug}`}
        onToggle={setPanelOpen}
      >
        <div className="space-y-4">
          {/* Network / DAI / Feed cap */}
          {isEditingNetwork ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <label className="text-muted-foreground w-16 shrink-0">Network:</label>
                <select
                  value={customNetwork ? '__custom__' : editNetworkOverride}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === '__custom__') {
                      setCustomNetwork(true);
                      setEditNetworkOverride('');
                    } else {
                      setCustomNetwork(false);
                      setEditNetworkOverride(v);
                    }
                  }}
                  className="flex-1 min-w-0 px-2 py-1 bg-secondary border border-border rounded"
                >
                  <option value="">Auto-detect</option>
                  {networks?.map((network) => (
                    <option key={network.id} value={network.id}>
                      {network.name}
                    </option>
                  ))}
                  {editNetworkOverride && !customNetwork &&
                    !(networks ?? []).some((n) => n.id === editNetworkOverride) && (
                    <option value={editNetworkOverride}>{editNetworkOverride}</option>
                  )}
                  <option value="__custom__">Custom network...</option>
                </select>
              </div>
              {customNetwork && (
                <>
                  <div className="flex items-center gap-2">
                    <label className="text-muted-foreground w-16 shrink-0">Name:</label>
                    <input
                      type="text"
                      value={editNetworkOverride}
                      onChange={(e) => setEditNetworkOverride(e.target.value)}
                      placeholder="Network name"
                      className="flex-1 min-w-0 px-2 py-1 bg-secondary border border-border rounded"
                    />
                  </div>
                  <p className="text-xs text-muted-foreground pl-[4.5rem]">
                    Feeds with the same name share cues.
                  </p>
                </>
              )}
              <div className="flex items-center gap-2">
                <label className="text-muted-foreground w-16 shrink-0">DAI:</label>
                <input
                  type="text"
                  value={editDaiPlatform}
                  onChange={(e) => setEditDaiPlatform(e.target.value)}
                  placeholder="e.g., megaphone, acast"
                  className="flex-1 min-w-0 px-2 py-1 bg-secondary border border-border rounded"
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-muted-foreground w-16 shrink-0">Feed cap:</label>
                <input
                  type="number"
                  value={editMaxEpisodes}
                  onChange={(e) => setEditMaxEpisodes(e.target.value)}
                  placeholder="300"
                  min={10}
                  max={500}
                  className="w-20 px-2 py-1 bg-secondary border border-border rounded"
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={saveNetworkEdit}
                  disabled={updateMutation.isPending}
                  className={`px-2 py-1 text-xs ${btnPrimary} rounded disabled:opacity-50`}
                >
                  {updateMutation.isPending ? 'Saving...' : 'Save'}
                </button>
                <button
                  onClick={() => setIsEditingNetwork(false)}
                  className="px-2 py-1 text-xs bg-muted text-muted-foreground rounded hover:bg-accent"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3 flex-wrap text-sm">
              {(feed.networkIdOverride || feed.networkId) && (
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  feed.networkIdOverride
                    ? 'bg-orange-500/20 text-orange-600 dark:text-orange-400'
                    : 'bg-green-500/20 text-success'
                }`}>
                  {feed.networkIdOverride ? 'Override' : 'Detected'}: {feed.networkIdOverride || feed.networkId}
                </span>
              )}
              {feed.daiPlatform && (
                <span className="px-2 py-0.5 bg-purple-500/20 text-purple-600 dark:text-purple-400 rounded text-xs font-medium">
                  DAI: {feed.daiPlatform}
                </span>
              )}
              <span className="text-muted-foreground">
                Feed cap: <span className="text-foreground">{feed.maxEpisodes || 300}</span>
              </span>
              <button
                onClick={startEditingNetwork}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                {feed.networkIdOverride || feed.networkId || feed.daiPlatform ? 'Edit' : '+ Add network'}
              </button>
            </div>
          )}

          {/* Source RSS URL (#484): the feed MinusPod pulls from, not the
              URL subscribers use. Editable with validate-then-refresh. */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Source feed:</span>
            {isEditingSourceUrl ? (
              <div className="flex flex-col gap-1 flex-1 min-w-0">
                <input
                  type="url"
                  value={editSourceUrl}
                  onChange={(e) => setEditSourceUrl(e.target.value)}
                  placeholder="https://example.com/feed.xml"
                  className="w-full min-w-0 px-2 py-1 bg-secondary border border-border rounded"
                />
                <p className="text-xs text-warning">
                  Points this feed at a different source URL. Existing episodes are
                  kept and matched by GUID; the feed refreshes right after saving.
                </p>
                {sourceUrlError && (
                  <p className="text-xs text-destructive">{sourceUrlError}</p>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={saveSourceUrl}
                    disabled={sourceUrlMutation.isPending}
                    className={`px-2 py-1 text-xs ${btnPrimary} rounded disabled:opacity-50`}
                  >
                    {sourceUrlMutation.isPending ? 'Validating...' : 'Save'}
                  </button>
                  <button
                    onClick={() => setIsEditingSourceUrl(false)}
                    disabled={sourceUrlMutation.isPending}
                    className="px-2 py-1 text-xs bg-muted text-muted-foreground rounded hover:bg-accent disabled:opacity-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-1 flex-1 min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <code
                    className="text-xs bg-secondary px-2 py-1 rounded truncate min-w-0"
                    title={feed.sourceUrl}
                  >
                    {feed.sourceUrl}
                  </code>
                  <CopyButton text={feed.sourceUrl} label="Copy source URL" labelClassName="sr-only" />
                  <button
                    onClick={startEditingSourceUrl}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    Edit
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  The original feed MinusPod pulls episodes from. Not the URL you subscribe to.
                </p>
              </div>
            )}
          </div>

          {/* Auto-Process Control */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0">Auto-Process:</span>
            <div className="flex items-center gap-2 flex-wrap">
              <TriStateSelect
                value={feed.autoProcessOverride}
                onChange={(next) => updateMutation.mutate({ autoProcessOverride: next })}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0"
              />
              {feed.autoProcessOverride !== null && feed.autoProcessOverride !== undefined && (
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  feed.autoProcessOverride
                    ? 'bg-green-500/20 text-success'
                    : 'bg-red-500/20 text-red-600 dark:text-red-400'
                }`}>
                  {feed.autoProcessOverride ? 'Enabled' : 'Disabled'}
                </span>
              )}
            </div>
          </div>

          {/* Episode title blacklist: skip episodes whose title matches a glob pattern */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">
              Skip episodes by title:
            </span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              {(feed.titleSkipPatterns ?? []).length > 0 && (
                <div className="flex flex-wrap gap-1 mb-1">
                  {feed.titleSkipPatterns!.map((p) => (
                    <span
                      key={p}
                      className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded bg-blue-500/15 text-blue-700 dark:text-blue-400"
                    >
                      {p}
                      <button
                        type="button"
                        onClick={() => removeTitleSkipPattern(p)}
                        disabled={updateMutation.isPending}
                        className="text-blue-700/60 dark:text-blue-400/60 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
                        aria-label={`Remove ${p}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex items-center gap-2">
                {!addingTitleSkipPattern ? (
                  <button
                    type="button"
                    onClick={() => setAddingTitleSkipPattern(true)}
                    disabled={updateMutation.isPending}
                    className={`px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50`}
                  >
                    + Add pattern
                  </button>
                ) : (
                  <>
                    <input
                      type="text"
                      autoFocus
                      value={titleSkipPatternInput}
                      onChange={(e) => setTitleSkipPatternInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          addTitleSkipPattern();
                        }
                      }}
                      placeholder="JRE MMA Show *"
                      aria-label="New title pattern"
                      className="px-2 py-1 text-xs bg-secondary border border-border rounded flex-1 min-w-0"
                    />
                    <button
                      type="button"
                      onClick={addTitleSkipPattern}
                      disabled={updateMutation.isPending || !titleSkipPatternInput.trim()}
                      className={`px-2 py-1 text-xs rounded ${btnOutline} disabled:opacity-50`}
                    >
                      Add
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setAddingTitleSkipPattern(false);
                        setTitleSkipPatternInput('');
                      }}
                      className={`px-2 py-1 text-xs rounded ${btnOutline}`}
                    >
                      Cancel
                    </button>
                  </>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Episodes whose titles match a pattern are not processed. Use * as a wildcard, for example JRE MMA Show *.
              </p>
            </div>
          </div>

          {/* Served-feed visibility for a title-blacklisted episode */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-1.5">
              Skipped episodes:
            </span>
            <select
              value={feed.titleSkipAction ?? 'serve_original'}
              onChange={(e) => updateMutation.mutate({
                titleSkipAction: e.target.value as UpdateFeedPayload['titleSkipAction'],
              })}
              disabled={updateMutation.isPending}
              className="px-2 py-1.5 text-sm bg-secondary border border-border rounded self-start min-w-0 max-w-full disabled:opacity-50"
              aria-label="Skipped episodes"
            >
              <option value="serve_original">Keep in feed with original audio</option>
              <option value="hide">Hide from feed</option>
            </select>
          </div>

          {/* Single preset canonicalizing detectionMode/skipAdDetection/passthroughEnabled;
              those legacy fields stay available for external API callers. */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <label htmlFor="processing-mode" className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-1.5">
              Processing mode:
            </label>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              <select
                id="processing-mode"
                value={processingMode}
                onChange={(e) => updateMutation.mutate({
                  processingMode: e.target.value as UpdateFeedPayload['processingMode'],
                })}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded self-start min-w-0 max-w-full disabled:opacity-50"
              >
                <option value="standard">Standard (detect and cut ads)</option>
                <option value="keep_content">Keep content only (experimental)</option>
                <option value="skip_detection">Skip ad detection (transcripts and chapters only)</option>
                <option value="passthrough">Pass-through (serve upstream audio untouched)</option>
                <option value="cue_only" disabled={!cueOnlyEligible}>
                  Cue-only (cut from audio cue templates, no LLM) (experimental)
                </option>
              </select>
              {!cueOnlyEligible && (
                <p className="text-xs text-muted-foreground">
                  Cue-only needs one enabled ad-break start template and one enabled
                  ad-break end template. Mark one of each below to turn it on.
                </p>
              )}
              <p className={PROCESSING_MODE_HINTS[processingMode].className}>
                {PROCESSING_MODE_HINTS[processingMode].text}
              </p>
              {processingMode === 'cue_only' && (
                <div className="flex flex-col gap-3 pt-1">
                  <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3">
                    <label htmlFor="cue-only-safety" className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0">
                      Cue-only safety:
                    </label>
                    <select
                      id="cue-only-safety"
                      value={feed.cueOnlySafety ?? 'hold_new'}
                      onChange={(e) => updateMutation.mutate({
                        cueOnlySafety: e.target.value as NonNullable<UpdateFeedPayload['cueOnlySafety']>,
                      })}
                      disabled={updateMutation.isPending}
                      className="px-2 py-1.5 text-sm bg-secondary border border-border rounded self-start min-w-0 disabled:opacity-50"
                    >
                      <option value="hold_new">Hold new templates for review</option>
                      <option value="auto_cut">Auto-cut at high confidence</option>
                    </select>
                  </div>
                  <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3">
                    <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">
                      Transcription:
                    </span>
                    <div className="flex flex-col gap-1 flex-1 min-w-0">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <ToggleSwitch
                          checked={feed.skipTranscription === true}
                          onChange={(v) => updateMutation.mutate({ skipTranscription: v })}
                          disabled={updateMutation.isPending}
                          ariaLabel="Skip transcription"
                        />
                        <span>Skip transcription</span>
                      </label>
                      <p className="text-xs text-warning">
                        Generated chapters stop. Chapters already published by the show
                        still shift to match the cut audio. These episodes lose
                        transcript search and subtitles.
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Per-feed chapter mode */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-1.5">Chapters:</span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              <select
                value={feed.chaptersMode || 'auto'}
                onChange={(e) => updateMutation.mutate({ chaptersMode: e.target.value as 'auto' | 'generate' | 'off' })}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded self-start min-w-0 max-w-full disabled:opacity-50"
                aria-label="Chapters"
              >
                <option value="auto">Auto</option>
                <option value="generate">Always generate</option>
                <option value="off">Off</option>
              </select>
              <p className="text-xs text-muted-foreground">
                Auto keeps the podcast&apos;s own chapters with timestamps shifted to
                the ad-free audio, and generates chapters when an episode has too
                few. Always generate replaces them; Off leaves them untouched.
              </p>
            </div>
          </div>

          {/* Per-feed auto-process queue priority (#625) */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-1.5">Queue priority:</span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              <select
                value={feed.queuePriority || 'normal'}
                onChange={(e) => updateMutation.mutate({
                  queuePriority: e.target.value as 'high' | 'normal' | 'low',
                })}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded self-start min-w-0 max-w-full disabled:opacity-50"
                aria-label="Queue priority"
              >
                <option value="high">High</option>
                <option value="normal">Normal</option>
                <option value="low">Low</option>
              </select>
              <p className="text-xs text-muted-foreground">
                High processes before other queued episodes. Low runs only when nothing else is waiting.
              </p>
            </div>
          </div>

          {/* Per-feed transcription language override */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0">Language:</span>
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={feed.languageOverride ?? ''}
                onChange={(e) => {
                  const v = e.target.value;
                  updateMutation.mutate({ languageOverride: v === '' ? null : v });
                }}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0 disabled:opacity-50"
              >
                <option value="">Global default</option>
                <option value="auto">Auto-detect (multilingual)</option>
                {WHISPER_LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.name} ({l.code})
                  </option>
                ))}
              </select>
              {feed.languageOverride && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                  Override: {labelForLanguage(feed.languageOverride)}
                </span>
              )}
            </div>
          </div>

          {/* Hide unprocessed episodes from the served feed */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0">Hide unprocessed:</span>
            <div className="flex items-center gap-2 flex-wrap">
              <TriStateSelect
                value={feed.onlyExposeProcessedEpisodes}
                onChange={(next) => updateMutation.mutate({ onlyExposeProcessedEpisodes: next })}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0"
              />
              {feed.onlyExposeProcessedEpisodes !== null && feed.onlyExposeProcessedEpisodes !== undefined && (
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  feed.onlyExposeProcessedEpisodes
                    ? 'bg-green-500/20 text-success'
                    : 'bg-red-500/20 text-red-600 dark:text-red-400'
                }`}>
                  {feed.onlyExposeProcessedEpisodes ? 'Hiding' : 'Showing all'}
                </span>
              )}
            </div>
          </div>

          {/* Served-feed GUID scheme (#598) */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Episode GUIDs:</span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              <label className="flex items-center gap-2 cursor-pointer">
                <ToggleSwitch
                  checked={feed.ownEpisodeGuids === true}
                  onChange={(v) => updateMutation.mutate({ ownEpisodeGuids: v })}
                  disabled={updateMutation.isPending}
                  ariaLabel="Serve MinusPod episode IDs"
                />
                <span>Serve MinusPod episode IDs</span>
              </label>
              <p className="text-xs text-warning">
                Uses MinusPod&apos;s own episode IDs as RSS GUIDs instead of the publisher&apos;s.
                Switching this on an existing feed makes subscribed apps treat every
                episode as new once. New feeds start with this on.
              </p>
            </div>
          </div>

          {/* Feed tags (inline basic row; simple enough not to collapse) */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Tags:</span>
            <div className="flex-1 min-w-0">
              <FeedTagsEditor slug={slug} />
            </div>
          </div>

          {/* Segment actions (issue #565): per-feed remove/beep/keep overrides,
              show-segment detection, and the bulk re-render trigger. */}
          <CollapsibleSection
            title="Segment actions"
            subtitle="Per-feed overrides, show-segment detection, and re-rendering already-processed episodes"
            defaultOpen={false}
            storageKey={`feed-segment-actions-${slug}`}
          >
            <div className="flex flex-col gap-3 pt-1">
              <p className="text-sm text-muted-foreground">
                Choose what happens to each kind of detected segment. Remove cuts it out, Beep replaces it with a tone, Keep leaves it in.
              </p>
              <div className="space-y-2">
                {SEGMENT_CATEGORIES.map((category) => {
                  const override = segmentOverrides[category];
                  const globalValue = settings?.segmentCategoryActions?.value?.[category] ?? DEFAULT_SEGMENT_ACTION;
                  const effective = override ?? globalValue;
                  return (
                    <div key={category} className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-3 text-sm">
                      <div className="min-w-0">
                        <span className="text-muted-foreground block">{SEGMENT_CATEGORY_LABELS[category]}</span>
                        <span className="text-xs text-muted-foreground/70 block">{SEGMENT_CATEGORY_DESCRIPTIONS[category]}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <SegmentActionToggle
                          value={effective}
                          muted={override === undefined}
                          disabled={updateMutation.isPending}
                          ariaLabel={`${SEGMENT_CATEGORY_LABELS[category]} action`}
                          onChange={(action) => setSegmentActionOverride(category, action)}
                        />
                        {override === undefined ? (
                          <span className="px-2 py-0.5 rounded text-xs font-medium bg-secondary text-muted-foreground">
                            Inherit
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => clearSegmentActionOverride(category)}
                            disabled={updateMutation.isPending}
                            className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              {segmentActionError && <p className="text-xs text-destructive">{segmentActionError}</p>}

              {/* Show-segment detection (issue #565): off by default. */}
              <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm pt-2 border-t border-border">
                <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-1.5">Show segments:</span>
                <div className="flex flex-col gap-1 flex-1 min-w-0">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <ToggleSwitch
                      checked={feed.detectShowSegments === true}
                      onChange={(v) => updateMutation.mutate({ detectShowSegments: v })}
                      disabled={updateMutation.isPending}
                      ariaLabel="Detect show segments"
                    />
                    <span>Detect show segments</span>
                  </label>
                  <p className="text-xs text-muted-foreground">
                    Finds the show&apos;s intro, outro and credits, and preview bumpers so you
                    can keep or cut them by category. Rough edges where music dominates.
                  </p>
                </div>
              </div>

              {/* Bulk re-render (issue #565): apply the current segment
                  actions to every already-processed episode. */}
              <div className="pt-2 border-t border-border flex flex-col gap-2">
                <button
                  type="button"
                  onClick={handleRerenderClick}
                  disabled={rerenderMutation.isPending}
                  className={`self-start whitespace-nowrap px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors`}
                >
                  {rerenderMutation.isPending ? 'Re-rendering...' : 'Re-render episodes'}
                </button>
                <p className="text-xs text-muted-foreground">
                  Applies the current segment actions to every processed episode with a retained original.
                </p>
                {rerenderResult && (
                  <p className="text-xs text-muted-foreground">
                    {rerenderResult.queued} episode{rerenderResult.queued === 1 ? '' : 's'} queued, {rerenderResult.skipped} skipped.
                  </p>
                )}
                {rerenderError && <p className="text-xs text-destructive">{rerenderError}</p>}
              </div>
            </div>
          </CollapsibleSection>

          {/* Cue tuning overrides (collapsible, advanced knobs) */}
          <CollapsibleSection
            title="Cue tuning overrides"
            defaultOpen={false}
            storageKey={`feed-cue-tuning-${slug}`}
          >
            <div className="flex flex-col gap-3 pt-1">
              {/* Cue match threshold */}
              <CueOverrideRow label="Cue threshold" min={CUE_SCORE_MIN} max={CUE_SCORE_MAX} step={0.01}
                value={cueScoreInput} setValue={setCueScoreInput} feedValue={feed.cueTemplateScoreOverride}
                hint="Empty = use global" formatOverride={(v) => v.toFixed(2)}
                placeholder={
                  settings?.audioCueTemplateScore?.value != null
                    ? String(settings.audioCueTemplateScore.value)
                    : '0.75'
                }
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(cueScoreInput, feed.cueTemplateScoreOverride,
                  'cueTemplateScoreOverride', CUE_SCORE_MIN, CUE_SCORE_MAX,
                  () => setCueScoreInput(feed.cueTemplateScoreOverride != null ? String(feed.cueTemplateScoreOverride) : ''))} />

              {/* create-from-pairs tri-state. The badge trails the hint so the
                  select stays first in the column, sharing the left edge every
                  other control in this section uses. */}
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
                <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0">Pair synthesis:</span>
                <div className="flex items-center gap-2 flex-wrap">
                  <TriStateSelect
                    value={feed.cueCreateFromPairsOverride}
                    onChange={(next) => updateMutation.mutate({ cueCreateFromPairsOverride: next })}
                    disabled={updateMutation.isPending}
                    className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0"
                  />
                  <span className="text-xs text-muted-foreground">Empty = use global</span>
                  <ExperimentalBadge />
                  {feed.cueCreateFromPairsOverride != null && (
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                      Override: {feed.cueCreateFromPairsOverride ? 'on' : 'off'}
                    </span>
                  )}
                </div>
              </div>

              <CueOverrideRow label="Pair min break" min={1} max={600} step={1}
                value={pairMinInput} setValue={setPairMinInput} feedValue={feed.cuePairMinBreakOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(pairMinInput, feed.cuePairMinBreakOverride,
                  'cuePairMinBreakOverride', 1, 600,
                  () => setPairMinInput(s(feed.cuePairMinBreakOverride)))} />
              <CueOverrideRow label="Pair max break" min={1} max={3600} step={1}
                value={pairMaxInput} setValue={setPairMaxInput} feedValue={feed.cuePairMaxBreakOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(pairMaxInput, feed.cuePairMaxBreakOverride,
                  'cuePairMaxBreakOverride', 1, 3600,
                  () => setPairMaxInput(s(feed.cuePairMaxBreakOverride)))} />
              <CueOverrideRow label="Pair max fraction" min={0} max={1} step={0.05}
                value={pairFracInput} setValue={setPairFracInput} feedValue={feed.cuePairMaxBreakFractionOverride}
                hint="0-1, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(pairFracInput, feed.cuePairMaxBreakFractionOverride,
                  'cuePairMaxBreakFractionOverride', 0, 1,
                  () => setPairFracInput(s(feed.cuePairMaxBreakFractionOverride)))} />
              <CueOverrideRow label="Snap confidence" min={0} max={1} step={0.01}
                value={snapConfInput} setValue={setSnapConfInput} feedValue={feed.cueSnapConfidenceOverride}
                hint="0-1, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(snapConfInput, feed.cueSnapConfidenceOverride,
                  'cueSnapConfidenceOverride', 0, 1,
                  () => setSnapConfInput(s(feed.cueSnapConfidenceOverride)))} />
              <CueOverrideRow label="Snap lead" min={0.5} max={30} step={0.5}
                value={snapLeadInput} setValue={setSnapLeadInput} feedValue={feed.cueSnapLeadOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(snapLeadInput, feed.cueSnapLeadOverride,
                  'cueSnapLeadOverride', 0.5, 30,
                  () => setSnapLeadInput(s(feed.cueSnapLeadOverride)))} />
              <CueOverrideRow label="Snap lag" min={0.5} max={30} step={0.5}
                value={snapLagInput} setValue={setSnapLagInput} feedValue={feed.cueSnapLagOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(snapLagInput, feed.cueSnapLagOverride,
                  'cueSnapLagOverride', 0.5, 30,
                  () => setSnapLagInput(s(feed.cueSnapLagOverride)))} />
            </div>
          </CollapsibleSection>

          {/* Advanced settings (collapsible; rarely-changed knobs) */}
          <CollapsibleSection
            title="Advanced"
            subtitle="Cut snapping, ad review holds, and cross-fetch"
            defaultOpen={false}
            storageKey={`feed-advanced-${slug}`}
          >
            <div className="flex flex-col gap-3 pt-1">
              {/* Boundary-snap opt-ins (simple flags; off unless enabled here) */}
              {(
                [
                  {
                    label: 'Silence snap:',
                    field: 'silenceSnapEnabled' as const,
                    ariaLabel: 'Snap cuts to silence',
                    toggleLabel: 'Snap cuts to silence',
                    warning: 'Moves cut edges to nearby silence; a bad match can clip speech.',
                  },
                  {
                    label: 'Transition snap:',
                    field: 'transitionSnapEnabled' as const,
                    ariaLabel: 'Snap to content transitions',
                    toggleLabel: 'Snap to content transitions',
                    warning: 'Snaps cut edges to transition cues; verify results on this feed first.',
                  },
                ] satisfies Array<{
                  label: string;
                  field: keyof Pick<UpdateFeedPayload, 'silenceSnapEnabled' | 'transitionSnapEnabled'>;
                  ariaLabel: string;
                  toggleLabel: string;
                  warning: string;
                }>
              ).map(({ label, field, ariaLabel, toggleLabel, warning }) => (
                <div key={field} className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
                  <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">{label}</span>
                  <div className="flex flex-col gap-1 flex-1 min-w-0">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <ToggleSwitch
                        checked={feed[field] === true}
                        onChange={(v) => updateMutation.mutate({ [field]: v })}
                        disabled={updateMutation.isPending}
                        ariaLabel={ariaLabel}
                      />
                      <span>{toggleLabel}</span>
                    </label>
                    <p className="text-xs text-warning">
                      {warning}
                    </p>
                  </div>
                </div>
              ))}

              {/* Max ad duration override (Phase C held-for-review) */}
              <CueOverrideRow label="Max ad duration" min={1} max={3600} step={1}
                value={maxAdDurInput} setValue={setMaxAdDurInput} feedValue={feed.maxAdDurationOverride}
                hint="s, empty = no cap" placeholder="no cap"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(maxAdDurInput, feed.maxAdDurationOverride,
                  'maxAdDurationOverride', 1, 3600,
                  () => setMaxAdDurInput(s(feed.maxAdDurationOverride)))}
                description="Ads longer than this cap are held for review instead of cut. Changes apply on the next reprocess." />

              {/* Length past which an ad needs a confirmed sponsor */}
              <CueOverrideRow label="Sponsor needed over" min={30} max={3600} step={1}
                value={maxAdDurRejectInput} setValue={setMaxAdDurRejectInput}
                feedValue={feed.maxAdDurationRejectOverride}
                hint="s, empty = use global" placeholder="global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(maxAdDurRejectInput, feed.maxAdDurationRejectOverride,
                  'maxAdDurationRejectOverride', 30, 3600,
                  () => setMaxAdDurRejectInput(s(feed.maxAdDurationRejectOverride)))}
                description="Past this length an ad has to name a recognized sponsor to be cut; one that does not is held for review. Overrides the global setting for this feed." />

              {/* Cue-gated approval (Phase C held-for-review) */}
              <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
                <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Cue gating:</span>
                <div className="flex flex-col gap-1 flex-1 min-w-0">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <ToggleSwitch
                      checked={feed.cueGatedApproval === true}
                      onChange={(v) => updateMutation.mutate({ cueGatedApproval: v })}
                      disabled={updateMutation.isPending}
                      ariaLabel="Only cue-backed ads auto-cut"
                    />
                    <span>Only cue-backed ads auto-cut</span>
                  </label>
                  <p className="text-xs text-warning">
                    Only ads with audio cue evidence auto-cut; others are held for review. Enable cue templates first.
                  </p>
                </div>
              </div>

              {/* Skip verification pass (#599): pass 1 still cuts, pass 2 does not run */}
              <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
                <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Verification:</span>
                <div className="flex flex-col gap-1 flex-1 min-w-0">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <ToggleSwitch
                      checked={cueOnlyActive || feed.skipSecondPass === true}
                      onChange={(v) => updateMutation.mutate({ skipSecondPass: v })}
                      disabled={updateMutation.isPending || cueOnlyActive}
                      ariaLabel="Skip verification pass"
                    />
                    <span>Skip verification pass</span>
                  </label>
                  {cueOnlyActive ? (
                    <p className="text-xs text-muted-foreground">
                      Forced on by cue-only mode.
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      The verification pass re-scans the cut audio for ads the first pass
                      missed, at the cost of a second detection sweep. Turn this on for feeds
                      where the first pass is already reliable. It roughly halves the
                      ad-detection LLM spend. Held differential detections that the second
                      pass would have confirmed then wait for you instead.
                    </p>
                  )}
                </div>
              </div>

              {/* Cross-fetch differential (Layer 3): auto for DAI-looking feeds,
                  with explicit per-feed on/off overrides */}
              <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
                <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Cross-fetch diff:</span>
                <div className="flex flex-col gap-1 flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <select
                      value={feed.differentialFetchEnabled == null ? '' : String(feed.differentialFetchEnabled)}
                      onChange={(e) => {
                        const v = e.target.value;
                        updateMutation.mutate({
                          differentialFetchEnabled: v === '' ? null : v === 'true',
                        });
                      }}
                      disabled={updateMutation.isPending}
                      className="px-2 py-1.5 text-sm bg-secondary border border-border rounded min-w-0 disabled:opacity-50"
                      aria-label="Fetch each episode twice to find inserted ads"
                    >
                      <option value="">Auto (on for dynamic-ad feeds)</option>
                      <option value="true">On</option>
                      <option value="false">Off</option>
                    </select>
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-medium ${
                        feed.differentialFetchEffective
                          ? 'bg-rose-500/20 text-rose-600 dark:text-rose-400'
                          : 'bg-secondary text-muted-foreground'
                      }`}
                      title={feed.differentialFetchEffective
                        ? 'Based on this feed\'s recent episodes, new episodes are fetched twice and compared. Each episode\'s own audio URL makes the final call.'
                        : 'Based on this feed\'s recent episodes, new episodes are fetched once. Each episode\'s own audio URL makes the final call.'}
                    >
                      {feed.differentialFetchEffective ? 'Runs on this feed' : 'Not running'}
                    </span>
                    {feed.daiLikely && (
                      <span
                        className="px-2 py-0.5 rounded text-xs font-medium bg-rose-500/20 text-rose-600 dark:text-rose-400"
                        title="This feed's audio URLs route through a known dynamic ad insertion service."
                      >
                        DAI likely
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-warning">
                    Downloads a second copy of each new episode with a different client signature and compares them. Audio that differs between fetches was inserted dynamically. Doubles this feed's download count in the publisher's stats. Auto turns this on when the feed looks dynamically ad-served.
                  </p>
                </div>
              </div>
            </div>
          </CollapsibleSection>
        </div>
      </CollapsibleSection>
    </div>
  );
}

export default FeedSettingsPanel;
