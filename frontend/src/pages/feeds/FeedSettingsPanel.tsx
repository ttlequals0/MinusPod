import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getNetworks, updateFeed, UpdateFeedPayload, CUE_SCORE_MIN, CUE_SCORE_MAX } from '../../api/feeds';
import { getSettings } from '../../api/settings';
import type { Feed } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import CopyButton from '../../components/CopyButton';
import { FeedTagsEditor } from '../../components/FeedTagsEditor';
import ToggleSwitch from '../../components/ToggleSwitch';
import TriStateSelect from '../../components/TriStateSelect';
import { WHISPER_LANGUAGES, labelForLanguage } from '../../utils/whisperLanguages';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';

interface Props {
  feed: Feed;
  slug: string;
}

// Props for the per-field cue override row. Defined at module scope so its
// identity is stable across parent renders (avoids remount on every keystroke).
interface CueOverrideRowProps {
  label: string;
  min: number;
  max: number;
  step: number;
  value: string;
  setValue: (v: string) => void;
  field: keyof UpdateFeedPayload;
  feedValue: number | null | undefined;
  hint: string;
  onBlur: () => void;
  disabled: boolean;
  placeholder?: string;
  description?: string;
}

function CueOverrideRow({
  label, min, max, step, value, setValue, feedValue, hint, onBlur, disabled,
  placeholder = 'global', description,
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
          Override: {feedValue}
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
          <p className="text-xs text-amber-600 dark:text-amber-400">{description}</p>
        </div>
      ) : inputRow}
    </div>
  );
}

function FeedSettingsPanel({ feed, slug }: Props) {
  const queryClient = useQueryClient();
  const [isEditingNetwork, setIsEditingNetwork] = useState(false);
  const [editNetworkOverride, setEditNetworkOverride] = useState<string>('');
  const [customNetwork, setCustomNetwork] = useState(false);
  const [editDaiPlatform, setEditDaiPlatform] = useState('');
  const [editAutoProcessOverride, setEditAutoProcessOverride] = useState<string>('global');
  const [editMaxEpisodes, setEditMaxEpisodes] = useState<string>('');
  const [isEditingSourceUrl, setIsEditingSourceUrl] = useState(false);
  const [editSourceUrl, setEditSourceUrl] = useState('');
  const [sourceUrlError, setSourceUrlError] = useState<string | null>(null);

  const { data: networks } = useQuery({
    queryKey: ['networks'],
    queryFn: getNetworks,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

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
  });

  const updateMutation = useMutation({
    mutationFn: (data: UpdateFeedPayload) => updateFeed(slug, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      // Surface a newly-typed custom network in every other feed's dropdown.
      queryClient.invalidateQueries({ queryKey: ['networks'] });
      setIsEditingNetwork(false);
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

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="Feed settings"
        subtitle="Network, DAI platform, auto-processing, language, feed cap, cue match threshold, and tags"
        defaultOpen={false}
        storageKey={`feed-settings-${slug}`}
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
                  className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50"
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
                    : 'bg-green-500/20 text-green-600 dark:text-green-400'
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
                <p className="text-xs text-amber-600 dark:text-amber-400">
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
                    className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50"
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
                  The original feed MinusPod pulls episodes from -- not the URL you subscribe to.
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
                    ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                    : 'bg-red-500/20 text-red-600 dark:text-red-400'
                }`}>
                  {feed.autoProcessOverride ? 'Enabled' : 'Disabled'}
                </span>
              )}
            </div>
          </div>

          {/* Per-feed detection mode (experimental keep-content inversion) */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-1.5">Detection:</span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              <select
                value={feed.detectionMode || 'blacklist'}
                onChange={(e) => updateMutation.mutate({ detectionMode: e.target.value })}
                disabled={updateMutation.isPending}
                className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0 disabled:opacity-50"
              >
                <option value="blacklist">Remove ads (default)</option>
                <option value="keep_content">Keep content only (experimental)</option>
              </select>
              {feed.detectionMode === 'keep_content' && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  Removes everything the model does not mark as show content. For feeds with
                  unrecognizable inserted ads. Safety checks revert to normal removal when the
                  labeling looks off, but they can miss a single mislabeled stretch and cut real
                  audio. Check each episode.
                </p>
              )}
            </div>
          </div>

          {/* Per-feed cue match threshold override */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0">Cue threshold:</span>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="number"
                min={CUE_SCORE_MIN}
                max={CUE_SCORE_MAX}
                step={0.01}
                value={cueScoreInput}
                placeholder={
                  settings?.audioCueTemplateScore?.value != null
                    ? String(settings.audioCueTemplateScore.value)
                    : '0.75'
                }
                onChange={(e) => setCueScoreInput(e.target.value)}
                onBlur={() => commitFloat(
                  cueScoreInput,
                  feed.cueTemplateScoreOverride,
                  'cueTemplateScoreOverride',
                  CUE_SCORE_MIN, CUE_SCORE_MAX,
                  () => setCueScoreInput(feed.cueTemplateScoreOverride != null ? String(feed.cueTemplateScoreOverride) : ''),
                )}
                disabled={updateMutation.isPending}
                className="w-24 px-2 py-1.5 text-sm bg-secondary border border-border rounded disabled:opacity-50"
              />
              <span className="text-xs text-muted-foreground">Empty = use global</span>
              {feed.cueTemplateScoreOverride != null && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                  Override: {feed.cueTemplateScoreOverride.toFixed(2)}
                </span>
              )}
            </div>
          </div>

          {/* Cue tuning overrides (collapsible, advanced knobs) */}
          <CollapsibleSection
            title="Cue tuning overrides"
            defaultOpen={false}
          >
            <div className="flex flex-col gap-3 pt-1">
              {/* create-from-pairs tri-state */}
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
                  {feed.cueCreateFromPairsOverride != null && (
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-600 dark:text-blue-400">
                      Override: {feed.cueCreateFromPairsOverride ? 'on' : 'off'}
                    </span>
                  )}
                </div>
              </div>

              <CueOverrideRow label="Pair min break" min={1} max={600} step={1}
                value={pairMinInput} setValue={setPairMinInput}
                field="cuePairMinBreakOverride" feedValue={feed.cuePairMinBreakOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(pairMinInput, feed.cuePairMinBreakOverride,
                  'cuePairMinBreakOverride', 1, 600,
                  () => setPairMinInput(s(feed.cuePairMinBreakOverride)))} />
              <CueOverrideRow label="Pair max break" min={1} max={3600} step={1}
                value={pairMaxInput} setValue={setPairMaxInput}
                field="cuePairMaxBreakOverride" feedValue={feed.cuePairMaxBreakOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(pairMaxInput, feed.cuePairMaxBreakOverride,
                  'cuePairMaxBreakOverride', 1, 3600,
                  () => setPairMaxInput(s(feed.cuePairMaxBreakOverride)))} />
              <CueOverrideRow label="Pair max fraction" min={0} max={1} step={0.05}
                value={pairFracInput} setValue={setPairFracInput}
                field="cuePairMaxBreakFractionOverride" feedValue={feed.cuePairMaxBreakFractionOverride}
                hint="0-1, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(pairFracInput, feed.cuePairMaxBreakFractionOverride,
                  'cuePairMaxBreakFractionOverride', 0, 1,
                  () => setPairFracInput(s(feed.cuePairMaxBreakFractionOverride)))} />
              <CueOverrideRow label="Snap confidence" min={0} max={1} step={0.01}
                value={snapConfInput} setValue={setSnapConfInput}
                field="cueSnapConfidenceOverride" feedValue={feed.cueSnapConfidenceOverride}
                hint="0-1, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(snapConfInput, feed.cueSnapConfidenceOverride,
                  'cueSnapConfidenceOverride', 0, 1,
                  () => setSnapConfInput(s(feed.cueSnapConfidenceOverride)))} />
              <CueOverrideRow label="Snap lead" min={0.5} max={30} step={0.5}
                value={snapLeadInput} setValue={setSnapLeadInput}
                field="cueSnapLeadOverride" feedValue={feed.cueSnapLeadOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(snapLeadInput, feed.cueSnapLeadOverride,
                  'cueSnapLeadOverride', 0.5, 30,
                  () => setSnapLeadInput(s(feed.cueSnapLeadOverride)))} />
              <CueOverrideRow label="Snap lag" min={0.5} max={30} step={0.5}
                value={snapLagInput} setValue={setSnapLagInput}
                field="cueSnapLagOverride" feedValue={feed.cueSnapLagOverride}
                hint="s, empty = global"
                disabled={updateMutation.isPending}
                onBlur={() => commitFloat(snapLagInput, feed.cueSnapLagOverride,
                  'cueSnapLagOverride', 0.5, 30,
                  () => setSnapLagInput(s(feed.cueSnapLagOverride)))} />
            </div>
          </CollapsibleSection>

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
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  {warning}
                </p>
              </div>
            </div>
          ))}

          {/* Max ad duration override (Phase C held-for-review) */}
          <CueOverrideRow label="Max ad duration" min={1} max={3600} step={1}
            value={maxAdDurInput} setValue={setMaxAdDurInput}
            field="maxAdDurationOverride" feedValue={feed.maxAdDurationOverride}
            hint="s, empty = no cap" placeholder="no cap"
            disabled={updateMutation.isPending}
            onBlur={() => commitFloat(maxAdDurInput, feed.maxAdDurationOverride,
              'maxAdDurationOverride', 1, 3600,
              () => setMaxAdDurInput(s(feed.maxAdDurationOverride)))}
            description="Ads longer than this cap are held for review instead of cut. Changes apply on the next reprocess." />

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
              <p className="text-xs text-amber-600 dark:text-amber-400">
                Only ads with audio cue evidence auto-cut; others are held for review. Enable cue templates first.
              </p>
            </div>
          </div>

          {/* Cross-fetch differential opt-in (Layer 3) */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
            <span className="text-muted-foreground whitespace-nowrap sm:w-32 shrink-0 sm:pt-0.5">Cross-fetch diff:</span>
            <div className="flex flex-col gap-1 flex-1 min-w-0">
              <label className="flex items-center gap-2 cursor-pointer flex-wrap">
                <ToggleSwitch
                  checked={feed.differentialFetchEnabled === true}
                  onChange={(v) => updateMutation.mutate({ differentialFetchEnabled: v })}
                  disabled={updateMutation.isPending}
                  ariaLabel="Fetch each episode twice to find inserted ads"
                />
                <span>Fetch each episode twice to find inserted ads</span>
                {feed.daiLikely && (
                  <span
                    className="px-2 py-0.5 rounded text-xs font-medium bg-rose-500/20 text-rose-600 dark:text-rose-400"
                    title="This feed's audio URLs route through a known dynamic ad insertion service."
                  >
                    DAI likely
                  </span>
                )}
              </label>
              <p className="text-xs text-amber-600 dark:text-amber-400">
                Downloads a second copy of each new episode with a different client signature and compares them. Audio that differs between fetches was inserted dynamically. Doubles this feed's download count in the publisher's stats.
              </p>
              {feed.daiLikely && feed.differentialFetchEnabled !== true && (
                <p className="text-xs text-muted-foreground">
                  Looks like this feed uses dynamic ad insertion. Cross-fetch diff can help confirm it.
                </p>
              )}
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
                    ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                    : 'bg-red-500/20 text-red-600 dark:text-red-400'
                }`}>
                  {feed.onlyExposeProcessedEpisodes ? 'Hiding' : 'Showing all'}
                </span>
              )}
            </div>
          </div>

          {/* Feed tags (nested sub-section, same pattern as cue tuning overrides) */}
          <FeedTagsEditor slug={slug} />
        </div>
      </CollapsibleSection>
    </div>
  );
}

export default FeedSettingsPanel;
