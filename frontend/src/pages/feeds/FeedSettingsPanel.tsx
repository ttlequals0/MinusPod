import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getNetworks, updateFeed, UpdateFeedPayload, CUE_SCORE_MIN, CUE_SCORE_MAX } from '../../api/feeds';
import { getSettings } from '../../api/settings';
import type { Feed } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import TriStateSelect from '../../components/TriStateSelect';
import { WHISPER_LANGUAGES, labelForLanguage } from '../../utils/whisperLanguages';

interface Props {
  feed: Feed;
  slug: string;
}

function FeedSettingsPanel({ feed, slug }: Props) {
  const queryClient = useQueryClient();
  const [isEditingNetwork, setIsEditingNetwork] = useState(false);
  const [editNetworkOverride, setEditNetworkOverride] = useState<string>('');
  const [customNetwork, setCustomNetwork] = useState(false);
  const [editDaiPlatform, setEditDaiPlatform] = useState('');
  const [editAutoProcessOverride, setEditAutoProcessOverride] = useState<string>('global');
  const [editMaxEpisodes, setEditMaxEpisodes] = useState<string>('');

  const { data: networks } = useQuery({
    queryKey: ['networks'],
    queryFn: getNetworks,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const savedCueScore =
    feed.cueTemplateScoreOverride != null ? String(feed.cueTemplateScoreOverride) : '';
  const [cueScoreInput, setCueScoreInput] = useState<string>(savedCueScore);
  // Render-time reset when the server value changes (avoids a setState-in-effect).
  const [prevSavedCueScore, setPrevSavedCueScore] = useState<string>(savedCueScore);
  if (savedCueScore !== prevSavedCueScore) {
    setPrevSavedCueScore(savedCueScore);
    setCueScoreInput(savedCueScore);
  }

  const updateMutation = useMutation({
    mutationFn: (data: UpdateFeedPayload) => updateFeed(slug, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      // Surface a newly-typed custom network in every other feed's dropdown.
      queryClient.invalidateQueries({ queryKey: ['networks'] });
      setIsEditingNetwork(false);
    },
  });

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
        subtitle="Network, DAI platform, auto-processing, language, feed cap, and cue match threshold"
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
                onBlur={() => {
                  const raw = cueScoreInput.trim();
                  if (raw === '') {
                    updateMutation.mutate({ cueTemplateScoreOverride: null });
                  } else {
                    const v = parseFloat(raw);
                    if (!Number.isNaN(v) && v >= CUE_SCORE_MIN && v <= CUE_SCORE_MAX) {
                      updateMutation.mutate({ cueTemplateScoreOverride: v });
                    } else {
                      // Invalid input: revert to the persisted value so the
                      // field doesn't keep showing unsaved text.
                      setCueScoreInput(feed.cueTemplateScoreOverride != null
                        ? String(feed.cueTemplateScoreOverride) : '');
                    }
                  }
                }}
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
        </div>
      </CollapsibleSection>
    </div>
  );
}

export default FeedSettingsPanel;
