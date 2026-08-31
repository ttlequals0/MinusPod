import { useState, useRef, useMemo } from 'react';
import { useParams, Link } from 'react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  episodeOriginalUrl, getEpisode, getFeed, getOriginalTranscript, reprocessEpisode, regenerateChapters,
  updateLocalEpisode, uploadLocalEpisodeArtwork,
} from '../api/feeds';
import type { LocalEpisodePatch } from '../api/feeds';
import { submitCorrection } from '../api/patterns';
import { getErrorMessage } from '../api/client';
import { SegmentCategoryBadge, KeptBadge } from '../components/SegmentCategoryBadge';
import PrevNextLink from '../components/PrevNextLink';
import LoadingSpinner from '../components/LoadingSpinner';
import Artwork from '../components/Artwork';
import { episodeArtworkSrc } from '../utils/artworkUrl';
import { EPISODE_STATUS_COLORS, isFailedStatus } from '../utils/episodeStatus';
import { DETECTION_STAGE_META } from '../utils/detectionStage';
import { CORROBORATION_CLASS, CORROBORATION_META } from '../utils/corroboration';
import { formatConfidence } from '../utils/confidence';
import AdEditor, { AdCorrection } from '../components/AdEditor';
import AdReviewModal from '../components/AdReviewModal';
import type { AdSegment, Feed, EpisodeDetail as EpisodeDetailApi } from '../api/types';
import PatternLink from '../components/PatternLink';
import ExpandableText from '../components/ExpandableText';
import RichText from '../components/RichText';
import CollapsibleSection, { useCollapsibleOpen } from '../components/CollapsibleSection';
import CueDetectionsSection from '../components/CueDetectionsSection';
import CueCandidatesSection from '../components/CueCandidatesSection';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import { useSyncFromQuery } from '../hooks/useSyncFromQuery';
import { formatStorage, formatDuration } from './settings/settingsUtils';
import { formatDate, formatTimestamp, toDatetimeLocalInput, fromDatetimeLocalInput } from '../utils/format';
import { useAuditionPlayer } from '../hooks/useAuditionPlayer';
import { AuditionPlayButton } from '../components/AuditionPlayButton';
import { rowActionBtn } from '../components/rowActionStyles';
import { StageBadge } from '../components/StageBadge';
import ProcessingRunsTable from '../components/ProcessingRunsTable';
import EpisodeLogsCard from '../components/EpisodeLogsCard';
import { btnDestructive, btnPrimary, btnSecondary } from '../components/buttonStyles';
import { focusRing } from '../components/fieldStyles';

function btnLabel(status: string, idle: string): string {
  if (status === 'saving') return 'Saving...';
  if (status === 'success') return 'Saved!';
  if (status === 'error') return 'Error!';
  return idle;
}

// Modes with LLM ad detection disabled server-side; retrying detection 409s.
const REDETECT_DISABLED_MODES = new Set<Feed['processingMode']>([
  'passthrough', 'skip_detection', 'cue_only',
]);
const REDETECT_DISABLED_MODE_LABELS: Partial<Record<NonNullable<Feed['processingMode']>, string>> = {
  passthrough: 'pass-through',
  skip_detection: 'skip ad detection',
  cue_only: 'cue-only',
};

function btnClass(status: string, idleClass: string): string {
  if (status === 'success') return 'bg-success/20 text-success';
  if (status === 'error') return 'bg-destructive/20 text-destructive';
  return idleClass;
}

function TranscriptBlock({ text }: { text: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <pre className="whitespace-pre-wrap text-sm text-muted-foreground font-sans">
        {text}
      </pre>
    </div>
  );
}

// Row-identity payload the corrections API keys on. One builder so the
// reason coercion stays consistent across the modal and the row buttons.
function toOriginalAd(segment: AdSegment) {
  return {
    start: segment.start,
    end: segment.end,
    confidence: segment.confidence,
    reason: segment.reason || '',
  };
}

// Pencil icon button that opens a held/rejected row in the waveform
// editor (issue #563). Callers own the gating.
function OpenEditorButton({ onClick, testId }: { onClick: () => void; testId: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open in editor"
      title="Open in editor"
      data-testid={testId}
      className={`p-1.5 rounded ${btnSecondary} transition-colors shrink-0 touch-manipulation ${focusRing}`}
    >
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
      </svg>
    </button>
  );
}

// Local episode ids are minted as sNNeNN (2-3 digit season, 2-4 digit
// episode), from upload_local_episode's `f's{season:02d}e{episode_number:02d}'`
// (the same scheme local_import.py's _TOKEN_RE parses). The detail endpoint
// now echoes seasonNumber/episodeNumber directly (they're the authoritative
// values -- the id is minted once at upload and never renamed, so it can go
// stale relative to them after an edit); parsing the id is only a fallback
// for a backend response that omits those fields.
const LOCAL_EPISODE_ID_RE = /^s(\d{2,3})e(\d{2,4})$/i;

function parseLocalEpisodeId(id: string): { season: number; episode: number } | null {
  const m = LOCAL_EPISODE_ID_RE.exec(id);
  if (!m) return null;
  return { season: parseInt(m[1], 10), episode: parseInt(m[2], 10) };
}

const editFieldCls = 'w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring';

// "Edit metadata" section shown only for episodes of a local feed (there's
// no upstream RSS to derive title/description/dates from). Local feeds only:
// callers gate rendering on feed.feedType === 'local'.
function EpisodeMetadataEditSection({ slug, episode }: { slug: string; episode: EpisodeDetailApi }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(episode.title);
  const [description, setDescription] = useState(episode.description ?? '');
  const [season, setSeason] = useState(() =>
    String(episode.seasonNumber ?? parseLocalEpisodeId(episode.id)?.season ?? ''));
  const [episodeNum, setEpisodeNum] = useState(() =>
    String(episode.episodeNumber ?? parseLocalEpisodeId(episode.id)?.episode ?? ''));
  const [publishedAt, setPublishedAt] = useState(toDatetimeLocalInput(episode.published));
  const [saved, setSaved] = useState(false);

  // Reseed the form when the episode object identity changes: a successful
  // save, a background refetch, or navigating to a different episode via
  // prev/next (the component instance is reused, only props change), same
  // idiom LocalFeedPanel uses for its feed metadata form.
  useSyncFromQuery(episode, (ep) => {
    const parsed = parseLocalEpisodeId(ep.id);
    setTitle(ep.title);
    setDescription(ep.description ?? '');
    setSeason(String(ep.seasonNumber ?? parsed?.season ?? ''));
    setEpisodeNum(String(ep.episodeNumber ?? parsed?.episode ?? ''));
    setPublishedAt(toDatetimeLocalInput(ep.published));
  });

  const mutation = useMutation({
    mutationFn: () => {
      const payload: LocalEpisodePatch = {
        title: title.trim() || null,
        description: description.trim() || null,
      };
      const s = parseInt(season, 10);
      const e = parseInt(episodeNum, 10);
      if (!Number.isNaN(s)) payload.season = s;
      if (!Number.isNaN(e)) payload.episode = e;
      const iso = fromDatetimeLocalInput(publishedAt);
      if (iso) payload.publishedAt = iso;
      return updateLocalEpisode(slug, episode.id, payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episode.id] });
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const artworkMutation = useMutation({
    mutationFn: (file: File) => uploadLocalEpisodeArtwork(slug, episode.id, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['episode', slug, episode.id] }),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate();
  };

  return (
    <div className="mb-6">
      <CollapsibleSection title="Edit metadata" defaultOpen={false} storageKey="episode-edit-metadata">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="ep-edit-title" className="block text-sm font-medium text-foreground mb-2">Title</label>
            <input id="ep-edit-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} required className={editFieldCls} />
          </div>
          <div>
            <label htmlFor="ep-edit-description" className="block text-sm font-medium text-foreground mb-2">Description</label>
            <textarea id="ep-edit-description" value={description} onChange={(e) => setDescription(e.target.value)} rows={6} className={editFieldCls} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="ep-edit-season" className="block text-sm font-medium text-foreground mb-2">Season</label>
              <input id="ep-edit-season" type="number" min={1} value={season} onChange={(e) => setSeason(e.target.value)} className={editFieldCls} />
            </div>
            <div>
              <label htmlFor="ep-edit-episode" className="block text-sm font-medium text-foreground mb-2">Episode</label>
              <input id="ep-edit-episode" type="number" min={1} value={episodeNum} onChange={(e) => setEpisodeNum(e.target.value)} className={editFieldCls} />
            </div>
          </div>
          <div>
            <label htmlFor="ep-edit-published" className="block text-sm font-medium text-foreground mb-2">Published</label>
            <input id="ep-edit-published" type="datetime-local" value={publishedAt} onChange={(e) => setPublishedAt(e.target.value)} className={editFieldCls} />
          </div>
          <div>
            <label htmlFor="ep-edit-artwork" className="block text-sm font-medium text-foreground mb-2">Artwork</label>
            <input
              id="ep-edit-artwork"
              type="file"
              accept="image/jpeg,image/png"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = '';
                if (file) artworkMutation.mutate(file);
              }}
              className={`block w-full text-sm text-muted-foreground file:mr-3 file:px-3 file:py-1.5 file:rounded file:border-0 file:text-sm ${btnSecondary} file:transition-colors ${focusRing}`}
            />
            {artworkMutation.isPending && <p className="mt-1 text-sm text-muted-foreground">Uploading...</p>}
            {artworkMutation.isSuccess && <p className="mt-1 text-sm text-success">Artwork updated.</p>}
            {artworkMutation.isError && (
              <p className="mt-1 text-sm text-destructive">{getErrorMessage(artworkMutation.error, 'Artwork upload failed')}</p>
            )}
          </div>
          {mutation.isError && (
            <p className="text-sm text-destructive">{getErrorMessage(mutation.error, 'Could not save')}</p>
          )}
          <button
            type="submit"
            disabled={mutation.isPending || !title.trim()}
            className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors ${focusRing}`}
          >
            {mutation.isPending ? 'Saving...' : saved ? 'Saved' : 'Save metadata'}
          </button>
        </form>
      </CollapsibleSection>
    </div>
  );
}

// Save status type for visual feedback
type SaveStatus = 'idle' | 'saving' | 'success' | 'error';

function EpisodeDetail() {
  const { slug, episodeId } = useParams<{ slug: string; episodeId: string }>();
  const [showEditor, setShowEditor] = useState(false);
  const [createModeRequested, setCreateModeRequested] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  // Transient banner for a rejected correction or reprocess; the backend's
  // own message is shown verbatim.
  const [correctionError, setCorrectionError] = useState<string | null>(null);
  const [showReprocessMenu, setShowReprocessMenu] = useState(false);
  const [editorSelectedAdIndex, setEditorSelectedAdIndex] = useState(0);
  // Held/rejected row currently open in the standalone waveform editor
  // (issue #563). Independent of showEditor/AdEditor state.
  const [reviewMarker, setReviewMarker] = useState<{ segment: AdSegment; key: string; fromHeld: boolean } | null>(null);
  const [savedScrollY, setSavedScrollY] = useState<number | null>(null);
  const [reviewMode, setReviewMode] = useLocalStorageState<'processed' | 'original'>(
    'ad-editor-review-mode',
    'processed',
  );
  // Tracks the Original Transcript section's open state (mirrors the
  // CollapsibleSection's persisted flag, same storage key) so the full
  // transcript is only fetched while the section is actually open -- not on
  // every episode page forever after one use.
  const [originalTranscriptOpen, setOriginalTranscriptOpen] =
    useCollapsibleOpen('episode-original-transcript');
  // When a "Confirm & Recut" action fires, this flag signals the correctionMutation
  // onSuccess to chain a recut immediately after the correction is stored.
  const pendingRecutRef = useRef(false);
  const editorRef = useRef<HTMLDivElement>(null);

  const queryClient = useQueryClient();

  const { data: episode, isLoading, error } = useQuery({
    queryKey: ['episode', slug, episodeId],
    queryFn: () => getEpisode(slug!, episodeId!),
    enabled: !!slug && !!episodeId,
  });

  // Fetched only for ``artworkUrl``, the fallback when the episode
  // declares no cover of its own.
  const { data: feed } = useQuery({
    queryKey: ['feed', slug],
    queryFn: () => getFeed(slug!),
    enabled: !!slug,
  });

  const { data: originalTranscript, isError: originalTranscriptError } = useQuery({
    queryKey: ['originalTranscript', slug, episodeId],
    queryFn: () => getOriginalTranscript(slug!, episodeId!),
    enabled: originalTranscriptOpen && !!slug && !!episodeId && !!episode?.originalTranscriptAvailable,
  });

  const reprocessMutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full' | 'llm' | 'recut') => reprocessEpisode(slug!, episodeId!, mode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
      setShowReprocessMenu(false);
    },
    // Processing is serialized by a lock, so a stale cached status leaves the
    // button enabled and the click is refused; showing the 409 stops it just
    // flickering with nothing to explain it (#707).
    onError: (error) => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
      setCorrectionError(getErrorMessage(error, 'Could not start reprocessing.'));
    },
  });

  const regenerateChaptersMutation = useMutation({
    mutationFn: () => regenerateChapters(slug!, episodeId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
    },
    onError: (error) => {
      console.error('Failed to regenerate chapters:', error);
    },
  });

  // Mutation for submitting ad corrections
  const correctionMutation = useMutation({
    mutationFn: (correction: AdCorrection) => {
      if (correction.type === 'create') {
        return submitCorrection(slug!, episodeId!, {
          type: 'create',
          start: correction.start,
          end: correction.end,
          sponsor: correction.sponsor,
          text_template: correction.text_template,
          scope: correction.scope,
          reason: correction.reason,
          category: correction.category,
        });
      }
      const oa = correction.originalAd!;
      return submitCorrection(slug!, episodeId!, {
        type: correction.type,
        original_ad: {
          start: oa.start,
          end: oa.end,
          pattern_id: oa.pattern_id,
          confidence: oa.confidence,
          reason: oa.reason,
          sponsor: oa.sponsor,
        },
        adjusted_start: correction.adjustedStart,
        adjusted_end: correction.adjustedEnd,
        sponsor: correction.sponsor,
        category: correction.category,
      });
    },
    onMutate: () => {
      setSaveStatus('saving');
      setCorrectionError(null);
      // A saved correction can remove the playing row (and its whole section)
      // on refetch, which would leave the windowed preview playing with no
      // visible owner. Stop it up front.
      markerAudition.stop();
    },
    onSuccess: () => {
      setSaveStatus('success');
      setTimeout(() => setSaveStatus('idle'), 2000);
      queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
      if (pendingRecutRef.current) {
        pendingRecutRef.current = false;
        reprocessMutation.mutate('recut');
      }
    },
    onError: (error) => {
      console.error('Failed to save correction:', error);
      setSaveStatus('error');
      // A keep-resolved marker (409) or any other rejection: surface the
      // backend's own message rather than special-casing the status code.
      setCorrectionError(getErrorMessage(error, 'Failed to save correction'));
      pendingRecutRef.current = false;
      setTimeout(() => setSaveStatus('idle'), 3000);
    },
  });

  // Handle ad corrections from AdEditor
  const handleCorrection = (correction: AdCorrection) => {
    correctionMutation.mutate(correction);
  };

  // Per-row save status for the Held-for-Review and Detections-Not-Cut rows.
  // Match on the full identity, not just start/end, so two markers that
  // happen to share boundaries don't both light up when only one is saving.
  const rowSaveStatus = (segment: {
    start: number; end: number; confidence: number; reason?: string;
  }): SaveStatus => {
    const mutAd = correctionMutation.variables?.originalAd;
    return mutAd?.start === segment.start &&
      mutAd?.end === segment.end &&
      mutAd?.confidence === segment.confidence &&
      mutAd?.reason === (segment.reason || '')
      ? saveStatus
      : 'idle';
  };

  // Open (or toggle) the editor from a fresh entry point. Reopening must land
  // on the first ad; a stale index from the last editing session would clamp
  // to the last ad (#564). handleJumpToAd below is the one entry point that
  // instead targets a specific ad, so it does not go through this reset.
  const openEditorFresh = (createMode: boolean) => {
    if (!showEditor) {
      setSavedScrollY(window.scrollY);
      setEditorSelectedAdIndex(0);
    }
    setCreateModeRequested(createMode);
    setShowEditor(createMode ? true : !showEditor);
  };

  // Jump to a specific ad in the editor. Sets the selected index so the modal
  // renders the right ad (and its own effect seeks audio to the ad start).
  // Without the index, the modal stays on whichever ad was last selected
  // (defaulting to 0 on first open).
  const handleJumpToAd = (adIndex: number) => {
    setEditorSelectedAdIndex(adIndex);
    if (!showEditor) {
      setShowEditor(true);
    }
    setTimeout(() => {
      editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  };

  // Convert ad markers to AdEditor format - memoized to prevent stale closures in editor
  const adMarkers = episode?.adMarkers;
  const detectedAds = useMemo(() => {
    if (!adMarkers) return [];
    return adMarkers.map((marker) => ({
      start: marker.start,
      end: marker.end,
      confidence: marker.confidence,
      reason: marker.reason || '',
      sponsor: marker.sponsor,
      pattern_id: undefined,
      detection_stage: marker.detection_stage || 'first_pass',
    }));
  }, [adMarkers]);

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return '';
    const mb = bytes / (1024 * 1024);
    return formatStorage(mb);
  };

  // Helper to find correction for an ad marker. 'create' corrections
  // have original_bounds=null (there is no original -- it's a net-new
  // marker); guard the dereference so iterating the corrections list
  // doesn't crash after a create save.
  const getAdCorrection = (start: number, end: number) => {
    return episode?.corrections?.find(c =>
      c.original_bounds &&
      c.original_bounds.start === start &&
      c.original_bounds.end === end
    );
  };

  // Batch approve (#509): with several held ads, Confirm ad is decision-only
  // and one Apply action recuts once for every confirmed hold. A confirm
  // correction matching a held marker is the durable "approved, not yet
  // applied" state (the card already shows it as the Confirmed badge).
  const heldMarkers = episode?.pendingReviewMarkers ?? [];
  // The marker flag is authoritative for 2.51+ approvals; the correction
  // join is the fallback for confirms recorded before the flag existed.
  const approvedHeldCount = heldMarkers.filter(
    (m) => m.approved || getAdCorrection(m.start, m.end)?.correction_type === 'confirm'
  ).length;
  // One-tap Confirm & Recut when this approval completes the review set:
  // a single held ad, or the last unapproved one of several.
  const oneTapRecut = !!episode?.hasOriginalAudio
    && heldMarkers.length - approvedHeldCount === 1;

  // Windowed playback for Held for Review and Detections Not Cut rows. Both
  // kinds of markers are never cut, and their times are in the original-audio
  // timeline, so the retained original is the correct source. No preload when
  // the original is gone -- the play buttons are hidden then and preload would
  // fire a wasted request.
  const markerAudioUrl = episodeOriginalUrl(slug!, episodeId!);
  const markerAudition = useAuditionPlayer(
    episode?.hasOriginalAudio ? markerAudioUrl : undefined);

  if (isLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  if (error || !episode) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive">Failed to load episode</p>
        <Link to={`/feeds/${slug}`} className={`text-primary hover:underline mt-2 inline-block ${focusRing}`}>
          Back to Feed
        </Link>
      </div>
    );
  }

  const failureReason =
    isFailedStatus(episode.status) && episode.error ? episode.error : undefined;

  // An episode that hasn't gone through the pipeline yet reads "Process",
  // not "Reprocess". Keyed on processedAt presence, not status: status
  // cycles back through pending/processing on every reprocess (a
  // reprocess-queued or currently-reprocessing episode must still read
  // "Reprocess"), while processedAt is set once on the first completed run
  // and never cleared by a later reprocess (reset_episode_for_reprocess in
  // reprocess_modes.py leaves it untouched), so it stays the reliable
  // "has this ever finished processing" signal throughout that window.
  const neverProcessed = !episode.processedAt;
  const reprocessLabel = neverProcessed ? 'Process' : 'Reprocess';

  // Detected-Ads header row 2: pass counts and time saved.
  const showPassCounts = episode.adsRemovedFirstPass !== undefined
    && episode.adsRemovedVerification !== undefined
    && episode.adsRemovedVerification > 0;
  const showTimeSaved = !!(episode.timeSaved && episode.timeSaved > 0);
  const hasPass2 = !!(episode.adsRemovedVerification && episode.adsRemovedVerification > 0);

  // Verification verdict (#519): the pipeline's second scan of the output
  // audio. Read from the latest run's stats blob, which records the count
  // only when the scan completed -- adsRemovedVerification defaults to 0,
  // so recuts, crashed scans, and pre-feature episodes would otherwise
  // falsely read as clean.
  const latestRun = episode.processingRuns?.length
    ? episode.processingRuns[episode.processingRuns.length - 1]
    : null;
  const verificationAdsCut = latestRun?.stats?.verificationAdsCut;
  const verificationVerdict =
    episode.status === 'completed' && verificationAdsCut != null
      ? verificationAdsCut === 0
        ? 'Verified: a second scan of the output audio found no remaining ads.'
        : `Verified: a second scan of the output audio found ${verificationAdsCut} more ad${verificationAdsCut === 1 ? '' : 's'} and cut ${verificationAdsCut === 1 ? 'it' : 'them'}.`
      : null;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <Link to={`/feeds/${slug}`} className={`text-primary hover:underline inline-block ${focusRing}`}>
          Back to Feed
        </Link>
        {episode.navigation && (
          <nav className="flex items-center gap-1.5" aria-label="Adjacent episodes">
            <PrevNextLink
              side="prev"
              label="Newer"
              to={episode.navigation.previous ? `/feeds/${slug}/episodes/${episode.navigation.previous.id}` : null}
              title={episode.navigation.previous ? `Newer episode: ${episode.navigation.previous.title}` : 'No newer episode'}
            />
            <PrevNextLink
              side="next"
              label="Older"
              to={episode.navigation.next ? `/feeds/${slug}/episodes/${episode.navigation.next.id}` : null}
              title={episode.navigation.next ? `Older episode: ${episode.navigation.next.title}` : 'No older episode'}
            />
          </nav>
        )}
      </div>

      {correctionError && (
        <div className="mb-4 p-3 rounded-lg bg-destructive/10 text-destructive text-sm flex items-center justify-between gap-3">
          <span>{correctionError}</span>
          <button
            type="button"
            onClick={() => setCorrectionError(null)}
            aria-label="Dismiss"
            className={`shrink-0 text-destructive hover:opacity-70 ${focusRing}`}
          >
            &times;
          </button>
        </div>
      )}

      <div className="bg-card rounded-lg border border-border p-4 sm:p-6 mb-6">
        <div className="flex gap-4">
          <div className="w-16 h-16 sm:w-24 sm:h-24 shrink-0">
            <Artwork
              src={episodeArtworkSrc(slug!, episode.id, episode.artworkUrl, feed?.artworkUrl)}
              alt="Podcast artwork"
              className="w-full h-full object-cover rounded-lg"
            />
          </div>
          <div className="flex flex-col gap-2 min-w-0">
            <h1 className="text-xl sm:text-2xl font-bold text-foreground">{episode.title}</h1>
            <div className="flex flex-wrap items-center gap-2 sm:gap-4 text-sm text-muted-foreground">
              <span>{formatDate(episode.published)}</span>
              {episode.status === 'completed' && episode.newDuration ? (
                <span>{formatDuration(episode.newDuration)}</span>
              ) : episode.duration ? (
                <span>{formatDuration(episode.duration)}</span>
              ) : null}
              {episode.fileSize && (
                <span>{formatFileSize(episode.fileSize)}</span>
              )}
              <span
                className={`px-2 py-0.5 rounded text-xs font-medium ${EPISODE_STATUS_COLORS[episode.status]}${failureReason ? ' cursor-help' : ''}`}
                title={failureReason}
              >
                {episode.status}
              </span>
              {episode.lowAdYield && (
                <span
                  className="px-2 py-0.5 rounded text-xs font-medium bg-warning/20 text-warning cursor-help"
                  title={`This run removed ${formatDuration(episode.lowAdYield.removedSeconds)} of ads; this feed's recent episodes average ${formatDuration(episode.lowAdYield.feedAverageSeconds)}. The downloaded copy may have arrived with unfilled ad slots, or ads were missed.`}
                >
                  Low ad yield
                </span>
              )}
              {episode.partialDetection && (
                <span
                  className="px-2 py-0.5 rounded text-xs font-medium bg-warning/20 text-warning cursor-help"
                  title={episode.partialDetection.reason}
                >
                  Partial detection
                </span>
              )}
              {episode.transcriptVttAvailable && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-c-blue/20 text-c-blue">
                  VTT
                </span>
              )}
              {episode.chaptersAvailable && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-c-purple/20 text-c-purple">
                  Chapters
                </span>
              )}
              {episode.daiDifferential && (
                <span
                  className={`px-2 py-0.5 rounded text-xs font-medium ${
                    episode.daiDifferential.status === 'ok'
                      ? 'bg-destructive/20 text-destructive'
                      : 'bg-muted text-muted-foreground'
                  }`}
                  title={
                    episode.daiDifferential.status === 'ok'
                      ? 'A second fetch of this episode differed from the first in the marked regions. The differing audio was dynamically inserted.'
                      : episode.daiDifferential.status === 'no_differential'
                      ? 'A second fetch matched the first everywhere it was compared. No differing ad fill was caught; the feed can still carry dynamic ads.'
                      : episode.daiDifferential.status === 'unreliable_reencode'
                      ? 'The second fetch was re-encoded end to end, so alignment could not lock on and nearly the whole file read as differing. The result was discarded rather than cutting real content.'
                      : episode.daiDifferential.error || 'The second fetch or the comparison failed.'
                  }
                >
                  {episode.daiDifferential.status === 'ok'
                    ? `Cross-fetch: ${episode.daiDifferential.regions.filter((r) => r.kind === 'differential').length} inserted`
                    : episode.daiDifferential.status === 'no_differential'
                    ? 'Cross-fetch: no diff'
                    : episode.daiDifferential.status === 'unreliable_reencode'
                    ? 'Cross-fetch: unreliable (re-encoded)'
                    : 'Cross-fetch: failed'}
                </span>
              )}
              {episode.llmCost != null && (
                <span className="text-xs text-muted-foreground">
                  LLM: ${episode.llmCost.toFixed(2)} ({episode.inputTokens != null && episode.inputTokens >= 1000 ? `${(episode.inputTokens / 1000).toFixed(1)}K` : episode.inputTokens ?? 0} in / {episode.outputTokens != null && episode.outputTokens >= 1000 ? `${(episode.outputTokens / 1000).toFixed(1)}K` : episode.outputTokens ?? 0} out)
                </span>
              )}
              <div className="relative">
                <button
                  onClick={() => setShowReprocessMenu(!showReprocessMenu)}
                  disabled={reprocessMutation.isPending || episode.status === 'processing'}
                  className={`px-2 py-0.5 text-xs sm:text-sm ${btnPrimary} rounded disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1 ${focusRing}`}
                >
                  {reprocessMutation.isPending
                    ? (neverProcessed ? 'Processing...' : 'Reprocessing...')
                    : reprocessLabel}
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>
                {showReprocessMenu && !reprocessMutation.isPending && episode.status !== 'processing' && (
                  <div className="absolute top-full right-0 mt-1 w-52 bg-card border border-border rounded-lg shadow-lg z-10 overflow-hidden">
                    <button
                      onClick={() => reprocessMutation.mutate('reprocess')}
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-accent ${focusRing}`}
                      title="Use learned patterns + AI analysis"
                    >
                      <div className="font-medium">{reprocessLabel}</div>
                      <div className="text-xs text-muted-foreground">Use patterns + AI</div>
                    </button>
                    <button
                      onClick={() => reprocessMutation.mutate('full')}
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-accent border-t border-border ${focusRing}`}
                      title="Skip pattern DB, AI analyzes everything fresh"
                    >
                      <div className="font-medium">Full Analysis</div>
                      <div className="text-xs text-muted-foreground">Skip patterns, AI only</div>
                    </button>
                    {episode.hasOriginalAudio && (
                      <button
                        onClick={() => reprocessMutation.mutate('recut')}
                        className={`w-full px-3 py-2 text-left text-sm hover:bg-accent border-t border-border ${focusRing}`}
                        title="Re-cut the original audio from your current ad edits (no transcription or AI)"
                      >
                        <div className="font-medium">Recut Audio</div>
                        <div className="text-xs text-muted-foreground">Apply edits, no AI</div>
                      </button>
                    )}
                    {episode.transcriptAvailable && (
                      <button
                        onClick={() => reprocessMutation.mutate('llm')}
                        disabled={REDETECT_DISABLED_MODES.has(feed?.processingMode)}
                        className={`w-full px-3 py-2 text-left text-sm hover:bg-accent border-t border-border disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent ${focusRing}`}
                        title={feed?.processingMode && REDETECT_DISABLED_MODES.has(feed.processingMode)
                          ? `Ad detection is off because this feed runs in ${REDETECT_DISABLED_MODE_LABELS[feed.processingMode]} mode`
                          : 'Re-run ad detection and re-cut using the existing transcript (skips re-transcription)'}
                      >
                        <div className="font-medium">Re-detect Ads</div>
                        <div className="text-xs text-muted-foreground">Keep transcript, re-cut</div>
                      </button>
                    )}
                    {episode.transcriptVttAvailable && (
                      <button
                        onClick={() => {
                          regenerateChaptersMutation.mutate();
                          setShowReprocessMenu(false);
                        }}
                        disabled={regenerateChaptersMutation.isPending}
                        className={`w-full px-3 py-2 text-left text-sm hover:bg-accent border-t border-border disabled:opacity-50 ${focusRing}`}
                        title="Regenerate chapters from existing transcript"
                      >
                        <div className="font-medium">Regenerate Chapters</div>
                        <div className="text-xs text-muted-foreground">Use existing transcript</div>
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {verificationVerdict && (
          <p className="mt-2 text-xs text-muted-foreground">{verificationVerdict}</p>
        )}

        {regenerateChaptersMutation.isPending && (
          <p className="mt-2 text-sm text-muted-foreground flex items-center gap-2">
            <LoadingSpinner size="sm" inline /> Regenerating chapters...
          </p>
        )}
        {regenerateChaptersMutation.isSuccess && (
          <p className="mt-2 text-sm text-success">Chapters regenerated.</p>
        )}
        {regenerateChaptersMutation.isError && (
          <p className="mt-2 text-sm text-destructive">
            {getErrorMessage(regenerateChaptersMutation.error, 'Failed to regenerate chapters')}
          </p>
        )}

        {failureReason && (
          <div className="mt-4 pt-4 border-t border-border">
            <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
              <p className="font-medium mb-1">
                {episode.status === 'permanently_failed'
                  ? 'Processing failed permanently'
                  : 'Processing failed'}
              </p>
              <p className="break-words">{failureReason}</p>
            </div>
          </div>
        )}

        {episode.partialDetection && (
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex items-center justify-between gap-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
              <span>
                The AI detection pass failed during processing. Ads were removed using pattern and cross-fetch evidence only, so some ads may remain.
              </span>
              <button
                type="button"
                onClick={() => reprocessMutation.mutate('llm')}
                disabled={reprocessMutation.isPending || episode.status === 'processing'}
                className={`shrink-0 px-3 py-1.5 text-xs sm:text-sm rounded ${btnSecondary} disabled:opacity-50 disabled:cursor-not-allowed ${focusRing}`}
              >
                Re-run detection
              </button>
            </div>
          </div>
        )}

        {episode.status === 'completed' && (
          <div className="mt-4 pt-4 border-t border-border">
            {/* processedUrl is server-built and carries the feed auth key when
                enabled; a hardcoded path would 401 under authenticated feeds. */}
            <audio controls className="w-full" src={episode.processedUrl ?? `/episodes/${slug}/${episode.id}.mp3`}>
              Your browser does not support the audio element.
            </audio>
            {(episode.transcriptVttAvailable || episode.chaptersAvailable) && (
              <div className="flex flex-wrap gap-2 mt-3">
                {episode.transcriptVttAvailable && episode.transcriptVttUrl && (
                  <a
                    href={episode.transcriptVttUrl}
                    download
                    className={`px-3 py-1 text-sm bg-c-blue/20 text-c-blue rounded hover:bg-c-blue/30 transition-colors ${focusRing}`}
                  >
                    Download VTT
                  </a>
                )}
                {episode.chaptersAvailable && episode.chaptersUrl && (
                  <a
                    href={episode.chaptersUrl}
                    download
                    className={`px-3 py-1 text-sm bg-c-purple/20 text-c-purple rounded hover:bg-c-purple/30 transition-colors ${focusRing}`}
                  >
                    Download Chapters
                  </a>
                )}
              </div>
            )}
          </div>
        )}

        {/* Local feeds retain the uploaded original even before processing
            runs (hasOriginalAudio since 2.93.2). Let the operator preview it
            from the detail page instead of only via the ad editor. Gated to
            local feeds and non-completed status so the processed-episode
            player above stays the only player once a run has finished.
            !processedAt additionally excludes a once-processed episode
            that's mid-reprocess or failed: status alone cycles back through
            pending/processing/failed on a reprocess, but processedAt is set
            once on the first completed run and never cleared afterward (see
            the neverProcessed comment below), so without this an episode
            that's already been through the pipeline once would misleadingly
            show "ad removal hasn't run yet" again. */}
        {episode.status !== 'completed' && !episode.processedAt
          && feed?.feedType === 'local' && episode.hasOriginalAudio && (
          <div className="mt-4 pt-4 border-t border-border">
            <audio controls className="w-full" src={markerAudioUrl}>
              Your browser does not support the audio element.
            </audio>
            <p className="mt-2 text-xs text-muted-foreground">
              Original audio; ad removal hasn&apos;t run yet.
            </p>
          </div>
        )}

        {episode.description && (
          <RichText
            html={episode.description}
            className="mt-4 block text-muted-foreground wrap-break-word"
          />
        )}
      </div>

      {feed?.feedType === 'local' && slug && episodeId && (
        <EpisodeMetadataEditSection slug={slug} episode={episode} />
      )}

      {/* "Add new ad" entry when the LLM found nothing (or before edit). */}
      {episode.status === 'completed' && episode.transcript &&
       (!episode.adMarkers || episode.adMarkers.length === 0) && (
        <div className="bg-card rounded-lg border border-border p-6 mb-6">
          {showEditor && createModeRequested ? (
            <AdEditor
              detectedAds={[]}
              audioDuration={episode.originalDuration ?? 0}
              onCorrection={handleCorrection}
              onClose={() => {
                setShowEditor(false);
                setCreateModeRequested(false);
              }}
              createMode={true}
            />
          ) : (
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold text-foreground">No ads detected</h2>
                <p className="text-sm text-muted-foreground">
                  Spotted an ad the detector missed? Mark it manually so the pattern matcher learns it.
                </p>
              </div>
              {/* Icon-only on mobile, full label on sm:+. Mirrors AdReviewModal. */}
              <button
                type="button"
                onClick={() => openEditorFresh(true)}
                aria-label="Add new ad"
                title="Add new ad"
                className={`shrink-0 inline-flex items-center gap-1 px-3 py-1.5 text-sm rounded-md ${btnPrimary} transition-colors whitespace-nowrap ${focusRing}`}
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
                <span className="hidden sm:inline">Add new ad</span>
              </button>
            </div>
          )}
        </div>
      )}

      {episode.adMarkers && episode.adMarkers.length > 0 && (
        <div className="bg-card rounded-lg border border-border p-6 mb-6">
          <div className="mb-4">
            {/* Row 1: Title + Edit button */}
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold text-foreground">
                Detected Ads ({episode.adMarkers.length})
              </h2>
              {episode.status === 'completed' && episode.transcript && (
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => openEditorFresh(false)}
                    aria-label={showEditor ? 'Hide editor' : 'Edit ads'}
                    title={showEditor ? 'Hide editor' : 'Edit ads'}
                    className={`inline-flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs sm:text-sm ${btnSecondary} rounded-md transition-colors whitespace-nowrap ${focusRing}`}
                  >
                    <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                    <span className="hidden sm:inline">{showEditor ? 'Hide Editor' : 'Edit Ads'}</span>
                  </button>
                  <button
                    onClick={() => openEditorFresh(true)}
                    aria-label="Add new ad"
                    title="Add new ad"
                    className={`inline-flex items-center gap-1.5 px-2 sm:px-3 py-1.5 text-xs sm:text-sm ${btnPrimary} rounded-md transition-colors whitespace-nowrap ${focusRing}`}
                  >
                    <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    <span className="hidden sm:inline">Add new ad</span>
                  </button>
                </div>
              )}
            </div>
            {/* Row 2: Detection stage info + time saved */}
            {(showPassCounts || showTimeSaved) && (
              <div className="mt-1 text-sm text-muted-foreground">
                {showPassCounts && (
                  <span>{episode.adsRemovedFirstPass} pass 1, {episode.adsRemovedVerification} pass 2</span>
                )}
                {showTimeSaved && (
                  <span className={hasPass2 ? 'ml-2' : ''}>
                    {hasPass2 ? '- ' : ''}{formatDuration(episode.timeSaved!)} time saved
                  </span>
                )}
              </div>
            )}
          </div>

          {/* AdEditor for reviewing/editing ad detections. The
              Processed/Original toggle and the "+ Add new ad" button
              now live INSIDE the modal header, so they remain reachable
              once the editor is open. */}
          {showEditor && episode.status === 'completed' && (
            <div className="mb-4" ref={editorRef}>
              <AdEditor
                detectedAds={detectedAds}
                audioDuration={episode.originalDuration ?? 0}
                audioUrl={episode.processedUrl ?? `/episodes/${slug}/${episode.id}.mp3`}
                audioMode={reviewMode}
                hasOriginal={!!episode.hasOriginalAudio}
                onAudioModeChange={setReviewMode}
                onCorrection={handleCorrection}
                onClose={() => {
                  setShowEditor(false);
                  setCreateModeRequested(false);
                  if (savedScrollY !== null) {
                    setTimeout(() => window.scrollTo(0, savedScrollY), 0);
                    setSavedScrollY(null);
                  }
                }}
                selectedAdIndex={editorSelectedAdIndex}
                onSelectedAdIndexChange={setEditorSelectedAdIndex}
                createMode={createModeRequested}
              />
            </div>
          )}

          <div className="space-y-3">
            {episode.adMarkers.map((segment, index) => (
              <div
                key={index}
                className="p-3 bg-secondary/50 rounded-lg"
              >
                {/* Row 1: Time, badges, jump button, confidence */}
                <div className="flex flex-wrap items-center gap-2">
                  {episode.hasOriginalAudio && (
                    <AuditionPlayButton
                      playing={markerAudition.playingKey === `detected-${segment.start}-${segment.end}`}
                      onClick={() => {
                        // Play the same timeframe the row displays: for
                        // reviewer-adjusted markers that is the reviewed
                        // pre-trim span, not the trimmed cut bounds.
                        const adjusted = segment.reviewer_verdict === 'adjust'
                          && segment.reviewer_original_start !== undefined
                          && segment.reviewer_original_end !== undefined;
                        markerAudition.toggle(
                          `detected-${segment.start}-${segment.end}`,
                          markerAudioUrl,
                          adjusted ? segment.reviewer_original_start! : segment.start,
                          adjusted ? segment.reviewer_original_end! : segment.end,
                        );
                      }}
                    />
                  )}
                  <span className="font-mono text-sm">
                    {segment.reviewer_verdict === 'adjust' && segment.reviewer_original_start !== undefined && segment.reviewer_original_end !== undefined
                      ? `${formatTimestamp(segment.reviewer_original_start)} - ${formatTimestamp(segment.reviewer_original_end)}`
                      : `${formatTimestamp(segment.start)} - ${formatTimestamp(segment.end)}`}
                  </span>
                  <SegmentCategoryBadge category={segment.category} />
                  {segment.actionApplied === 'keep' && <KeptBadge />}
                  {segment.detection_stage && DETECTION_STAGE_META[segment.detection_stage] && (
                    <StageBadge stage={segment.detection_stage} />
                  )}
                  {segment.corroborated_by && CORROBORATION_META[segment.corroborated_by] && (
                    <span
                      className={`px-1.5 py-0.5 text-xs rounded font-medium ${CORROBORATION_CLASS}`}
                      title={CORROBORATION_META[segment.corroborated_by].title}
                    >
                      {CORROBORATION_META[segment.corroborated_by].label}
                    </span>
                  )}
                  {segment.cue_snap && (
                    <span
                      className="px-1.5 py-0.5 text-xs rounded font-medium bg-c-purple/20 text-c-purple"
                      title={
                        (segment.cue_snap.start as Record<string, unknown> | undefined)?.cue_type === 'content_transition' ||
                        (segment.cue_snap.end as Record<string, unknown> | undefined)?.cue_type === 'content_transition'
                          ? "An audio cue snapped this ad's edge to the chime via content transition"
                          : "An audio cue snapped this ad's edge to the chime"
                      }
                    >
                      Cue snapped
                    </span>
                  )}
                  {segment.silence_snap && (
                    <span
                      className="px-1.5 py-0.5 text-xs rounded font-medium bg-c-teal/20 text-c-teal"
                      title="Ad edge snapped to nearby silence"
                    >
                      Silence snapped
                    </span>
                  )}
                  {segment.sponsor && (
                    <span
                      className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground"
                      title="Sponsor"
                    >
                      {segment.sponsor}
                    </span>
                  )}
                  {segment.reviewer_verdict === 'confirmed' && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-success/20 text-success" title={segment.reviewer_reasoning || 'Confirmed by reviewer'}>
                      Reviewer: confirmed
                    </span>
                  )}
                  {segment.reviewer_verdict === 'adjust' && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-c-teal/20 text-c-teal" title={segment.reviewer_reasoning || 'Boundaries adjusted by reviewer'}>
                      Reviewer: adjusted
                    </span>
                  )}
                  {segment.reviewer_verdict === 'resurrect' && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-warning/20 text-warning" title={segment.reviewer_reasoning || 'Resurrected by reviewer'}>
                      Reviewer: resurrected
                    </span>
                  )}
                  {segment.reviewer_verdict === 'failure' && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground" title="Reviewer LLM call failed; original detection kept">
                      Reviewer: skipped
                    </span>
                  )}
                  {episode.transcript && (
                    <button
                      onClick={() => handleJumpToAd(index)}
                      className={`px-3 py-1.5 sm:px-2 sm:py-0.5 text-xs bg-primary/10 text-primary rounded hover:bg-primary/20 active:bg-primary/30 transition-colors touch-manipulation min-h-[36px] sm:min-h-0 ${focusRing}`}
                      title="Jump to this ad in editor"
                    >
                      Jump
                    </button>
                  )}
                  {(() => {
                    const correction = getAdCorrection(segment.start, segment.end);
                    if (correction) {
                      return (
                        <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                          correction.correction_type === 'confirm'
                            ? 'bg-success/20 text-success'
                            : correction.correction_type === 'false_positive'
                            ? 'bg-warning/20 text-warning'
                            : 'bg-c-blue/20 text-c-blue'
                        }`}>
                          {correction.correction_type === 'confirm' ? 'Confirmed'
                           : correction.correction_type === 'false_positive' ? 'Not an ad'
                           : 'Adjusted'}
                        </span>
                      );
                    }
                    return null;
                  })()}
                  <span className="ml-auto text-sm text-muted-foreground whitespace-nowrap">
                    {formatConfidence(segment)}
                  </span>
                </div>
                {/* Row 2: Detector's own note about the match. Framed as
                    a "Match:" label so it doesn't read as a contradicting
                    sponsor when the field carries reviewer-overwritten
                    free text (e.g., boundary extension that swept up an
                    adjacent ad's content). */}
                {segment.reason && (
                  <ExpandableText
                    label="match"
                    className="text-sm text-muted-foreground mt-2"
                  >
                    <span className="font-medium">Match:</span>{' '}
                    <PatternLink reason={segment.reason} />
                  </ExpandableText>
                )}
                {segment.reviewer_verdict === 'adjust' && (
                  <p className="text-sm text-c-teal mt-1 font-mono">
                    Reviewer: {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                  </p>
                )}
                {segment.reviewer_verdict && segment.reviewer_reasoning && (
                  <ExpandableText
                    label="reviewer note"
                    clampLines={3}
                    className="text-xs text-muted-foreground mt-1 italic"
                  >
                    Reviewer: {segment.reviewer_reasoning}
                  </ExpandableText>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Shared windowed player for the Detected Ads, Held for Review, and
          Rejected Detections rows. Gated so the element (and its metadata
          preload) only exists when at least one playable row renders. */}
      {((episode.adMarkers?.length ?? 0) > 0 ||
        (episode.pendingReviewMarkers?.length ?? 0) > 0 ||
        (episode.rejectedAdMarkers?.length ?? 0) > 0) && markerAudition.audioElement}

      {/* Standalone waveform editor for one held/rejected marker (issue
          #563). Deliberately NOT routed through AdEditor: its moved-boundary
          submits map to type 'adjust', which does not approve a held marker.
          Here moved boundaries become confirm + adjusted bounds (the 2.60.0
          trimmed-approve flow), matching what the row buttons file. Bounds
          are in the original-audio timeline, so the modal is pinned to
          original audio (no mode toggle, no processed URL). */}
      {reviewMarker && (() => {
        const seg = reviewMarker.segment;
        const fromHeld = reviewMarker.fromHeld;
        const originalAd = toOriginalAd(seg);
        return (
          <AdReviewModal
            key={reviewMarker.key}
            mode="review"
            hasNext={false}
            item={{
              podcastSlug: slug!,
              episodeId: episodeId!,
              start: seg.start,
              end: seg.end,
              sponsor: seg.sponsor ?? null,
              reason: seg.reason || '',
              confidence: seg.confidence,
              detectionStage: seg.detection_stage || 'first_pass',
              patternId: null,
              correctedBounds:
                seg.reviewer_proposed_start != null && seg.reviewer_proposed_end != null
                  ? { start: seg.reviewer_proposed_start, end: seg.reviewer_proposed_end }
                  : null,
            }}
            audioMode="original"
            hasOriginal={!!episode.hasOriginalAudio}
            episodeDuration={episode.originalDuration ?? 0}
            boundsWindow={{ min: seg.start - 0.5, max: seg.end + 0.5 }}
            onSubmit={(s) => {
              if (s.kind === 'reject') {
                handleCorrection({ type: 'reject', originalAd });
              } else {
                // Mirror the held-row Confirm buttons: a confirm that
                // completes the review set chains a one-tap recut.
                // Rejected-row confirms never recut.
                if (fromHeld && oneTapRecut) {
                  pendingRecutRef.current = true;
                }
                if (s.kind === 'adjust') {
                  // boundsWindow keeps the modal's selection inside the
                  // window the backend accepts; this clamp is only a
                  // backstop against float edges.
                  handleCorrection({
                    type: 'confirm',
                    originalAd,
                    adjustedStart: Math.max(s.adjustedStart!, seg.start - 0.5),
                    adjustedEnd: Math.min(s.adjustedEnd!, seg.end + 0.5),
                    sponsor: s.sponsor,
                  });
                } else {
                  handleCorrection({ type: 'confirm', originalAd, sponsor: s.sponsor });
                }
              }
              setReviewMarker(null);
            }}
            onSkip={() => setReviewMarker(null)}
            onClose={() => setReviewMarker(null)}
          />
        );
      })()}

      {heldMarkers.length > 0 && (
        <div className="bg-card rounded-lg border border-warning/30 p-6 mb-6" data-testid="held-for-review-section">
          <h2 className="text-xl font-semibold text-foreground mb-4">
            Held for Review ({heldMarkers.length})
          </h2>
          <div className="space-y-3">
            {heldMarkers.map((segment, index) => {
              const correction = getAdCorrection(segment.start, segment.end);
              const holdTitle = segment.hold_reason === 'max_duration'
                ? "Exceeds the feed's max ad duration"
                : segment.hold_reason === 'no_cue_evidence'
                ? 'No audio-cue evidence'
                : segment.hold_reason === 'uncorroborated_tail'
                ? 'Trailing ad with no audio evidence to back it'
                : segment.hold_reason === 'reviewer_contradiction'
                ? 'The reviewer disagreed with the detected boundaries'
                : segment.hold_reason === 'no_splice_evidence'
                ? 'No splice artifact found at either edge'
                : segment.hold_reason === 'verification_miss'
                ? 'A standalone catch from the verification pass, held for a second opinion'
                : segment.hold_reason === 'differential_uncorroborated'
                ? 'Audio differs across fetches with no corroborating signal'
                : segment.hold_reason === 'large_vad_gap_extension'
                ? 'Untranscribed audio exceeded the safe adjacency-only extension limit'
                : segment.hold_reason === 'cue_template_unproven'
                ? "This cue template hasn't cut a confirmed ad yet"
                : segment.hold_reason === 'cue_low_confidence'
                ? 'The cue match fell below the cut-confidence threshold'
                : 'Held for manual review';
              const holdLabel = segment.hold_reason === 'verification_miss'
                ? 'Verification catch'
                : segment.hold_reason === 'differential_uncorroborated'
                ? 'Differential hold'
                : segment.hold_reason === 'large_vad_gap_extension'
                ? 'VAD extension limit'
                : segment.hold_reason === 'cue_template_unproven'
                ? 'Unproven cue'
                : segment.hold_reason === 'cue_low_confidence'
                ? 'Low-confidence cue'
                : 'Held';
              const rowStatus = rowSaveStatus(segment);
              const heldKey = `held-${segment.start}-${segment.end}`;
              const heldPlaying = markerAudition.playingKey === heldKey;
              const originalAd = toOriginalAd(segment);
              return (
                <div
                  key={index}
                  className="p-3 bg-warning/10 rounded-lg border border-warning/20"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {episode.hasOriginalAudio && (
                        <AuditionPlayButton
                          size="row"
                          playing={heldPlaying}
                          onClick={() => markerAudition.toggle(heldKey, markerAudioUrl, segment.start, segment.end)}
                        />
                      )}
                      {/* Decision surface: hidden once the row is decided,
                          matching the Confirm / Not an ad buttons. */}
                      {episode.hasOriginalAudio && !correction && !segment.approved && (
                        <OpenEditorButton
                          onClick={() => setReviewMarker({ segment, key: heldKey, fromHeld: true })}
                          testId={`open-editor-held-${index}`}
                        />
                      )}
                      <span className="font-mono text-sm">
                        {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                      </span>
                      <SegmentCategoryBadge category={segment.category} />
                      {segment.actionApplied === 'keep' && <KeptBadge />}
                      {segment.detection_stage && DETECTION_STAGE_META[segment.detection_stage] && (
                        <StageBadge stage={segment.detection_stage} />
                      )}
                      {segment.corroborated_by && CORROBORATION_META[segment.corroborated_by] && (
                        <span
                          className={`px-1.5 py-0.5 text-xs rounded font-medium ${CORROBORATION_CLASS}`}
                          title={CORROBORATION_META[segment.corroborated_by].title}
                        >
                          {CORROBORATION_META[segment.corroborated_by].label}
                        </span>
                      )}
                      <span
                        className="px-1.5 py-0.5 text-xs rounded font-medium bg-warning/20 text-warning"
                        title={holdTitle}
                      >
                        {holdLabel}
                      </span>
                      {(correction || segment.approved) && (
                        <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                          segment.approved || correction?.correction_type === 'confirm'
                            ? 'bg-success/20 text-success'
                            : 'bg-warning/20 text-warning'
                        }`}>
                          {segment.approved || correction?.correction_type === 'confirm' ? 'Confirmed' : 'Not an ad'}
                        </span>
                      )}
                    </div>
                    <span className="text-sm text-muted-foreground">
                      {formatConfidence(segment)}
                    </span>
                  </div>
                  {segment.validation?.flags && segment.validation.flags.length > 0 && (
                    <p className="text-sm text-warning mt-2">
                      {segment.validation.flags.join(', ')}
                    </p>
                  )}
                  {segment.reason && (
                    <p className="text-sm text-muted-foreground mt-1">
                      <span className="font-medium">Match:</span>{' '}
                      {segment.reason}
                    </p>
                  )}
                  {segment.hold_reason === 'reviewer_contradiction' && segment.reviewer_reasoning && (
                    <p className="text-sm text-muted-foreground mt-1">
                      <span className="font-medium">Reviewer:</span>{' '}
                      {segment.reviewer_reasoning}
                    </p>
                  )}
                  {!correction && !segment.approved && (
                    <div className="flex flex-col sm:flex-row gap-2 mt-3">
                      <button
                        onClick={() => {
                          if (oneTapRecut) {
                            pendingRecutRef.current = true;
                          }
                          handleCorrection({ type: 'confirm', originalAd });
                        }}
                        disabled={correctionMutation.isPending || reprocessMutation.isPending}
                        data-testid={`approve-recut-${index}`}
                        className={`flex-1 sm:flex-none ${rowActionBtn} ${btnClass(rowStatus, btnPrimary)} ${focusRing}`}
                      >
                        {btnLabel(rowStatus, oneTapRecut ? 'Confirm & Recut' : 'Confirm ad')}
                      </button>
                      {segment.reviewer_proposed_start != null && segment.reviewer_proposed_end != null && (
                        <button
                          onClick={() => {
                            if (oneTapRecut) {
                              pendingRecutRef.current = true;
                            }
                            handleCorrection({
                              type: 'confirm',
                              originalAd,
                              adjustedStart: segment.reviewer_proposed_start,
                              adjustedEnd: segment.reviewer_proposed_end,
                            });
                          }}
                          disabled={correctionMutation.isPending || reprocessMutation.isPending}
                          data-testid={`approve-trimmed-${index}`}
                          title="Approve only the span the reviewer identified as ad content; the rest of this marker stays in the episode"
                          className={`flex-1 sm:flex-none ${rowActionBtn} ${btnClass(rowStatus, btnSecondary)} ${focusRing}`}
                        >
                          {btnLabel(rowStatus,
                            `Confirm trimmed (${formatTimestamp(segment.reviewer_proposed_start)} - ${formatTimestamp(segment.reviewer_proposed_end)})`)}
                        </button>
                      )}
                      {!episode.hasOriginalAudio && rowStatus === 'success' && (
                        <span className="text-xs text-muted-foreground italic self-center">
                          Saved - applies on next reprocess
                        </span>
                      )}
                      <button
                        onClick={() => handleCorrection({ type: 'reject', originalAd })}
                        disabled={correctionMutation.isPending || reprocessMutation.isPending}
                        data-testid={`dismiss-${index}`}
                        className={`flex-1 sm:flex-none ${rowActionBtn} ${btnClass(rowStatus, `${btnDestructive} active:bg-destructive/80`)} ${focusRing}`}
                      >
                        {btnLabel(rowStatus, 'Not an ad')}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {episode.hasOriginalAudio && approvedHeldCount > 0 && (
            <div className="mt-4 pt-4 border-t border-warning/20 flex justify-end">
              <button
                onClick={() => reprocessMutation.mutate('recut')}
                disabled={correctionMutation.isPending || reprocessMutation.isPending
                  || episode.status === 'processing'}
                data-testid="apply-approved-recut"
                className={`w-full sm:w-auto ${rowActionBtn} ${btnPrimary} ${focusRing}`}
              >
                {`Apply ${approvedHeldCount} confirmed & recut`}
              </button>
            </div>
          )}
        </div>
      )}

      {episode.keptMarkers && episode.keptMarkers.length > 0 && (
        <div className="mb-6" data-testid="kept-segments-section">
          <CollapsibleSection
            title={`Kept segments (${episode.keptMarkers.length})`}
            subtitle="Detected, and left in the audio by your category actions"
            defaultOpen={false}
            storageKey="episode-kept-segments"
          >
            <div className="space-y-3">
              {episode.keptMarkers.map((segment, index) => (
                <div
                  key={index}
                  className="p-3 bg-secondary/50 rounded-lg"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    {episode.hasOriginalAudio && (
                      <AuditionPlayButton
                        label="this segment"
                        playing={markerAudition.playingKey === `kept-${segment.start}-${segment.end}`}
                        onClick={() => markerAudition.toggle(
                          `kept-${segment.start}-${segment.end}`,
                          markerAudioUrl,
                          segment.start,
                          segment.end,
                        )}
                      />
                    )}
                    <span className="font-mono text-sm">
                      {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                    </span>
                    <SegmentCategoryBadge category={segment.category} />
                    <KeptBadge />
                  </div>
                </div>
              ))}
            </div>
          </CollapsibleSection>
        </div>
      )}

      {episode.rejectedAdMarkers && episode.rejectedAdMarkers.length > 0 && (
        <div className="mb-6">
          <CollapsibleSection
            title={`Detections Not Cut (${episode.rejectedAdMarkers.length})`}
            subtitle="Flagged but kept in audio"
            defaultOpen={false}
            storageKey="episode-rejected-detections"
          >
          <p className="text-sm text-muted-foreground mb-4">
            These detections were flagged but the audio was kept, either
            because validation rejected them or because they were marked not
            an ad.
          </p>
          <div className="space-y-3">
            {episode.rejectedAdMarkers.map((segment, index) => (
              <div
                key={index}
                className="p-3 bg-destructive/10 rounded-lg border border-destructive/20"
              >
                {(() => {
                  const correction = getAdCorrection(segment.start, segment.end);
                  const rowStatus = rowSaveStatus(segment);
                  const rejectedKey = `rejected-${segment.start}-${segment.end}`;
                  const originalAd = toOriginalAd(segment);
                  const rejectedPlaying = markerAudition.playingKey === rejectedKey;
                  return (
                    <>
                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          {episode.hasOriginalAudio && (
                            <AuditionPlayButton
                              size="row"
                              playing={rejectedPlaying}
                              onClick={() => markerAudition.toggle(rejectedKey, markerAudioUrl, segment.start, segment.end)}
                            />
                          )}
                          {episode.hasOriginalAudio && !correction && (
                            <OpenEditorButton
                              onClick={() => setReviewMarker({ segment, key: rejectedKey, fromHeld: false })}
                              testId={`open-editor-rejected-${index}`}
                            />
                          )}
                          <span className="font-mono text-sm">
                            {formatTimestamp(segment.start)} - {formatTimestamp(segment.end)}
                          </span>
                          <SegmentCategoryBadge category={segment.category} />
                          {segment.actionApplied === 'keep' && <KeptBadge />}
                          <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-destructive/20 text-destructive">
                            Not cut
                          </span>
                          {segment.reviewer_verdict === 'reject' && (
                            <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-destructive/20 text-destructive" title={segment.reviewer_reasoning || 'Rejected by reviewer'}>
                              Reviewer: rejected
                            </span>
                          )}
                          {segment.reviewer_verdict === 'failure' && segment.source === 'reviewer' && (
                            <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground" title="Reviewer LLM call failed; validator decision kept">
                              Reviewer: skipped
                            </span>
                          )}
                          {correction && (
                            <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${
                              correction.correction_type === 'confirm'
                                ? 'bg-success/20 text-success'
                                : 'bg-warning/20 text-warning'
                            }`}>
                              {correction.correction_type === 'confirm' ? 'Confirmed' : 'Not an ad'}
                            </span>
                          )}
                        </div>
                        <span className="text-sm text-muted-foreground">
                          {formatConfidence(segment)}
                        </span>
                      </div>
                      {segment.validation?.flags && segment.validation.flags.length > 0 && (
                        <p className="text-sm text-destructive mt-2">
                          {segment.validation.flags.join(', ')}
                        </p>
                      )}
                      {segment.reason && (
                        <ExpandableText
                          label="match"
                          className="text-sm text-muted-foreground mt-1"
                        >
                          {segment.reason}
                        </ExpandableText>
                      )}
                      {!correction && (
                        <div className="flex flex-col sm:flex-row gap-2 mt-3">
                          <button
                            onClick={() => handleCorrection({ type: 'confirm', originalAd })}
                            disabled={correctionMutation.isPending}
                            className={`flex-1 sm:flex-none ${rowActionBtn} ${btnClass(rowStatus, btnPrimary)} ${focusRing}`}
                          >
                            {btnLabel(rowStatus, 'Confirm ad')}
                          </button>
                          <button
                            onClick={() => handleCorrection({ type: 'reject', originalAd })}
                            disabled={correctionMutation.isPending}
                            className={`flex-1 sm:flex-none ${rowActionBtn} ${btnClass(rowStatus, `${btnDestructive} active:bg-destructive/80`)} ${focusRing}`}
                          >
                            {btnLabel(rowStatus, 'Not an ad')}
                          </button>
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            ))}
          </div>
          </CollapsibleSection>
        </div>
      )}

      {episode.cueDetections && episode.cueDetections.length > 0 && slug && episodeId && (
        <div className="mb-6">
          <CueDetectionsSection
            slug={slug}
            episodeId={episodeId}
            detections={episode.cueDetections}
          />
        </div>
      )}

      {slug && episodeId && episode.hasOriginalAudio && (
        <div className="mb-6">
          <CueCandidatesSection
            slug={slug}
            episodeId={episodeId}
            episodeTitle={episode.title}
            episodeDuration={episode.originalDuration ?? episode.duration ?? 0}
            hasOriginalAudio={!!episode.hasOriginalAudio}
          />
        </div>
      )}

      {episode.transcript && (
        <div className="mb-6">
          <CollapsibleSection title="Transcript" defaultOpen={false} storageKey="episode-transcript">
            <TranscriptBlock text={episode.transcript} />
          </CollapsibleSection>
        </div>
      )}

      {episode.originalTranscriptAvailable && (
        <div className="mb-6">
          <CollapsibleSection
            title="Original Transcript"
            subtitle="Raw transcript before ads were removed"
            defaultOpen={false}
            storageKey="episode-original-transcript"
            onToggle={setOriginalTranscriptOpen}
          >
            {originalTranscript
              ? <TranscriptBlock text={originalTranscript} />
              : originalTranscriptError
                ? <p className="text-destructive">Failed to load original transcript</p>
                : <LoadingSpinner className="py-4" />
            }
          </CollapsibleSection>
        </div>
      )}

      {episode.processingRuns && episode.processingRuns.length > 0 && (
        <div className="mb-6">
          <CollapsibleSection
            title="Processing stats"
            subtitle="What each run downloaded, detected, and cut"
            defaultOpen={false}
            storageKey="episode-processing-stats"
          >
            <ProcessingRunsTable runs={episode.processingRuns} rssDuration={episode.rssDuration} />
          </CollapsibleSection>
        </div>
      )}

      {episode.processingRuns && episode.processingRuns.length > 0 && (
        <div className="mb-6">
          <CollapsibleSection
            title="Logs"
            subtitle="The pipeline log each run wrote"
            defaultOpen={false}
            storageKey="episode-run-logs"
          >
            <EpisodeLogsCard
              slug={slug!}
              episodeId={episodeId!}
              runs={episode.processingRuns}
            />
          </CollapsibleSection>
        </div>
      )}

    </div>
  );
}

export default EpisodeDetail;
