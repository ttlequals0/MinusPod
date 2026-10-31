import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { updateFeed, type UpdateFeedPayload } from '../../api/feeds';
import { getYouTubeSettings, YOUTUBE_KIND_LABELS, type YouTubeSettings } from '../../api/youtube';
import { getErrorMessage } from '../../api/client';
import type { Feed } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import { selectBase } from '../../components/fieldStyles';
import FeedArtworkUpload from './FeedArtworkUpload';
import FeedMetadataForm from './FeedMetadataForm';
import { fromTri, GlobalOverrideRow, rowLabelTop, toTri } from './OverrideRows';
import QueryError from '../../components/QueryError';
import { SkeletonRows } from '../../components/Skeleton';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';

type NumberField = 'ytProcessDelayMinutesOverride' | 'ytProbeAttemptsOverride' | 'ytProbeIntervalMinutesOverride';
type BoolField = 'ytLlmFallbackOverride' | 'ytLlmOnTopOverride' | 'ytCaptionsOverride' | 'ytHidePrivateOverride' | 'ytTranscribeOverride' | 'youtubeIncludeShorts';

function NumberOverrideRow({
  label, ariaLabel, value, globalValue, bounds, unit, disabled, onCommit, children,
}: {
  label: string;
  ariaLabel: string;
  value: number | null | undefined;
  globalValue: number | undefined;
  bounds: [number, number] | undefined;
  unit: string;
  disabled: boolean;
  onCommit: (value: number | null) => Promise<unknown>;
  children: string;
}) {
  const [custom, setCustom] = useState(false);
  const isCustom = custom || value != null;
  const [draft, setDraft] = useState<number | null>(null);
  const [touched, setTouched] = useState(false);
  const fieldId = useId();
  const [lo, hi] = bounds ?? [0, 100000];
  const invalid = touched && (draft === null || !Number.isInteger(draft) || draft < lo || draft > hi);
  const dirty = isCustom && touched && (invalid || draft !== value);
  useUnsavedChanges(dirty);

  async function commit() {
    if (!dirty || invalid) return;
    try {
      await onCommit(draft);
      setDraft(null);
      setTouched(false);
    } catch { /* The panel reports the mutation error. */ }
  }

  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
      <label htmlFor={`${fieldId}-mode`} className={rowLabelTop}>{label}</label>
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <select
            id={`${fieldId}-mode`}
            value={isCustom ? 'custom' : 'global'}
            onChange={(e) => {
              setDraft(null);
              setTouched(false);
              if (e.target.value === 'custom') {
                setCustom(true);
              } else {
                setCustom(false);
                if (value != null) void onCommit(null).catch(() => {});
              }
            }}
            disabled={disabled}
            className={`self-start w-full sm:w-auto min-w-0 max-w-full ${selectBase}`}
            aria-label={`${ariaLabel} mode`}
            aria-describedby={`${fieldId}-help`}
          >
            <option value="global">Use global ({globalValue ?? '-'} {unit})</option>
            <option value="custom">Custom</option>
          </select>
          {isCustom && (
            <>
              <DraftNumberInput
                value={value ?? null}
                fallback={globalValue ?? null}
                min={lo}
                max={hi}
                step={1}
                parse={parseOptionalNumber}
                onChange={(v) => { setDraft(v); setTouched(true); }}
                onBlur={() => void commit()}
                disabled={disabled}
                className="w-24"
                ariaLabel={ariaLabel}
                ariaInvalid={invalid}
                ariaDescribedBy={`${fieldId}-help${invalid ? ` ${fieldId}-error` : ''}`}
              />
              <span className="text-xs text-muted-foreground">{unit}</span>
            </>
          )}
        </div>
        <p id={`${fieldId}-help`} className="text-xs text-muted-foreground">{children}</p>
        {invalid && <p id={`${fieldId}-error`} role="alert" className="text-xs text-destructive">Enter a whole number from {lo} to {hi} {unit}.</p>}
      </div>
    </div>
  );
}

function YouTubeFeedPanel({ feed, slug }: { feed: Feed; slug: string }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const { data: globals, isPending, isError, isFetching, refetch } = useQuery({ queryKey: ['youtubeSettings'], queryFn: getYouTubeSettings });

  const mutation = useMutation({
    mutationFn: (payload: UpdateFeedPayload) => updateFeed(slug, payload),
    onSuccess: () => setError(null),
    onError: (err) => setError(getErrorMessage(err)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['feed', slug] }),
  });

  const setBool = (field: BoolField) => (v: 'on' | 'off' | null) =>
    mutation.mutate({ [field]: fromTri(v) });
  const setNumber = (field: NumberField) => (v: number | null) => mutation.mutateAsync({ [field]: v });
  const bound = (key: keyof YouTubeSettings['bounds']) => globals?.bounds[key];
  const pending = mutation.isPending;

  return (
    <div className="mb-6">
      <CollapsibleSection
        title="YouTube"
        subtitle="SponsorBlock, captions, transcription and listing options for this source."
        storageKey={`youtube-feed-${slug}`}
      >
        <div className="space-y-4">
          {globals?.enabled === false && (
            <p className="text-xs text-muted-foreground">YouTube feeds are turned off in Settings.</p>
          )}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
            <span className={rowLabelTop}>{YOUTUBE_KIND_LABELS[feed.youtubeSourceKind ?? 'channel']}</span>
            {feed.youtubeSourceUrl && (
              <a
                href={feed.youtubeSourceUrl}
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline"
              >
                Open on YouTube
              </a>
            )}
          </div>

          <FeedMetadataForm feed={feed} slug={slug} fields={['author', 'explicit', 'categories']} />

          <FeedArtworkUpload slug={slug} />

          {isError ? (
            <QueryError title="Could not load global YouTube settings." onRetry={() => void refetch()} retrying={isFetching} />
          ) : isPending ? <SkeletonRows count={3} /> : <>
          <GlobalOverrideRow
            label="LLM fallback"
            ariaLabel="LLM fallback"
            value={toTri(feed.ytLlmFallbackOverride)}
            globalOn={!!globals?.llmFallback}
            disabled={pending}
            onChange={setBool('ytLlmFallbackOverride')}
          >
            Run the ad detector when SponsorBlock has no marks for an episode.
          </GlobalOverrideRow>
          <GlobalOverrideRow
            label="Pass 1 detection"
            ariaLabel="Pass 1 detection"
            value={toTri(feed.ytLlmOnTopOverride)}
            globalOn={false}
            disabled={pending}
            onChange={setBool('ytLlmOnTopOverride')}
          >
            Run pass 1 LLM detection even when SponsorBlock has marks, and cut both.
          </GlobalOverrideRow>
          <GlobalOverrideRow
            label="Captions"
            ariaLabel="Use captions as transcript"
            value={toTri(feed.ytCaptionsOverride)}
            globalOn={!!globals?.captionsAsTranscript}
            disabled={pending}
            onChange={setBool('ytCaptionsOverride')}
          >
            Use YouTube captions instead of transcribing the audio when they exist.
          </GlobalOverrideRow>
          <GlobalOverrideRow
            label="Private"
            ariaLabel="Hide private and deleted episodes"
            value={toTri(feed.ytHidePrivateOverride)}
            globalOn={!!globals?.hidePrivate}
            disabled={pending}
            onChange={setBool('ytHidePrivateOverride')}
          >
            Hide private and deleted episodes. Off lists them, but they only download when the cookies account can watch them.
          </GlobalOverrideRow>
          <GlobalOverrideRow
            label="Transcribe"
            ariaLabel="Transcribe when captions are missing"
            value={toTri(feed.ytTranscribeOverride)}
            globalOn={!!globals?.transcribeFallback}
            disabled={pending}
            onChange={setBool('ytTranscribeOverride')}
          >
            Run the configured transcriber for videos with no captions. Off keeps such videos on SponsorBlock marks only.
          </GlobalOverrideRow>
          <GlobalOverrideRow
            label="Shorts"
            ariaLabel="Include Shorts"
            value={toTri(feed.youtubeIncludeShorts)}
            globalOn={false}
            disabled={pending}
            onChange={setBool('youtubeIncludeShorts')}
          >
            Include Shorts. Playlist feeds otherwise skip videos of 3 minutes or less; channel feeds otherwise list long-form uploads only.
          </GlobalOverrideRow>

          <NumberOverrideRow
            label="Delay"
            ariaLabel="Delay"
            value={feed.ytProcessDelayMinutesOverride}
            globalValue={globals?.processDelayMinutes}
            bounds={bound('processDelayMinutes')}
            unit="minutes"
            disabled={pending}
            onCommit={setNumber('ytProcessDelayMinutesOverride')}
          >
            How long to wait after a video appears before processing it.
          </NumberOverrideRow>
          <NumberOverrideRow
            label="Probe attempts"
            ariaLabel="Probe attempts"
            value={feed.ytProbeAttemptsOverride}
            globalValue={globals?.sbProbeAttempts}
            bounds={bound('sbProbeAttempts')}
            unit="attempts"
            disabled={pending}
            onCommit={setNumber('ytProbeAttemptsOverride')}
          >
            How many times to check SponsorBlock before giving up.
          </NumberOverrideRow>
          <NumberOverrideRow
            label="Probe interval"
            ariaLabel="Probe interval"
            value={feed.ytProbeIntervalMinutesOverride}
            globalValue={globals?.sbProbeIntervalMinutes}
            bounds={bound('sbProbeIntervalMinutes')}
            unit="minutes"
            disabled={pending}
            onCommit={setNumber('ytProbeIntervalMinutesOverride')}
          >
            Time between SponsorBlock checks.
          </NumberOverrideRow>

          </>}
          {pending && <p role="status" className="text-sm text-muted-foreground">Saving YouTube settings...</p>}
          {mutation.isSuccess && !error && <p role="status" className="text-sm text-success">YouTube settings saved.</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>
      </CollapsibleSection>
    </div>
  );
}

export default YouTubeFeedPanel;
