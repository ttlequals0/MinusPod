import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router';
import AdReviewModal, { AdReviewItem, AdReviewSubmit, AdCreateSubmit } from './AdReviewModal';
import type { PatternScope } from '../api/patterns';
import { btnGhost, btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';

export interface DetectedAd {
  start: number;
  end: number;
  confidence: number;
  reason: string;
  sponsor?: string;
  pattern_id?: number;
  detection_stage?: string;
  scope?: PatternScope;
  network_id?: string;
  category?: string | null;
  action_applied?: string | null;
}

export interface AdCorrection {
  type: 'confirm' | 'reject' | 'adjust' | 'create' | 'recategorize';
  originalAd?: DetectedAd;
  adjustedStart?: number;
  adjustedEnd?: number;
  sponsor?: string;
  // create-only fields
  start?: number;
  end?: number;
  text_template?: string;
  scope?: 'podcast' | 'global';
  reason?: string;
  category?: string | null;
}

interface AdEditorProps {
  detectedAds: DetectedAd[];
  audioDuration: number;
  audioUrl?: string;
  onCorrection: (correction: AdCorrection) => void;
  // Awaitable submission path, used only for a multi-span create so the
  // panel can confirm or fail each run's save before moving to the next.
  onCorrectionAsync?: (correction: AdCorrection) => Promise<void>;
  onClose?: () => void;
  selectedAdIndex?: number;
  onSelectedAdIndexChange?: (index: number) => void;
  // When true, the editor opens directly in 'create' mode for marking a
  // net-new ad on this episode (instead of reviewing detected ads).
  createMode?: boolean;
  // Episode-level audio-mode toggle. The waveform editor honors this for
  // review mode and forces 'original' in create mode.
  audioMode?: 'processed' | 'original';
  onAudioModeChange?: (m: 'processed' | 'original') => void;
  hasOriginal?: boolean;
}

// Re-export for consumers
export type { AdReviewItem };

const ADD_BUTTON_BTN =
  `px-3 py-1.5 rounded-lg ${btnPrimary} text-sm transition-colors`;
const GHOST_BTN =
  `${btnGhost} transition-colors`;

export function AdEditor({
  detectedAds,
  audioDuration,
  audioUrl,
  onCorrection,
  onCorrectionAsync,
  onClose,
  selectedAdIndex: externalSelectedAdIndex,
  onSelectedAdIndexChange,
  createMode = false,
  audioMode = 'original',
  onAudioModeChange,
  hasOriginal = true,
}: AdEditorProps) {
  const { slug = '', episodeId = '' } = useParams<{ slug: string; episodeId: string }>();

  const [internalIndex, setInternalIndex] = useState(0);
  const selectedAdIndex = externalSelectedAdIndex ?? internalIndex;
  const setSelectedAdIndex = (i: number) => {
    if (onSelectedAdIndexChange) onSelectedAdIndexChange(i);
    else setInternalIndex(i);
  };

  // Initialized from the prop; flipped internally when the user clicks
  // the in-modal "+ Add new ad" button. The sync useEffect below only
  // syncs FALSE -> TRUE so the parent can re-open create mode on an
  // already-mounted editor, but a user-initiated close (Cancel) does
  // not get clobbered by the parent's prop on the next render. This
  // was the source of the "modal won't close" flicker before 2.2.8.
  const [internalCreateMode, setInternalCreateMode] = useState(createMode);
  const prevCreateModePropRef = useRef(createMode);
  useEffect(() => {
    if (!prevCreateModePropRef.current && createMode) {
      setInternalCreateMode(true);
    }
    prevCreateModePropRef.current = createMode;
  }, [createMode]);

  // 2.2.6 added a cameFromReview ref so Cancel from create returned to the
  // review modal when entered via the in-modal "+ Add new ad". Users found
  // the reappearing review modal more surprising than helpful (#TBD), so
  // Cancel/X now always closes the editor regardless of entry path. Save
  // still flips back to review via handleCreateSubmit below.

  const safeIndex =
    detectedAds.length > 0
      ? Math.max(0, Math.min(selectedAdIndex, detectedAds.length - 1))
      : 0;
  const ad = detectedAds[safeIndex];

  // In create mode the modal needs a placeholder item; the actual marker
  // bounds come from adStart/adEnd inside the modal.
  const item: AdReviewItem = internalCreateMode || !ad
    ? {
        podcastSlug: slug,
        episodeId,
        start: 0,
        end: Math.min(60, audioDuration),
        sponsor: null,
        reason: null,
        confidence: null,
        detectionStage: 'manual',
        patternId: null,
        correctedBounds: null,
      }
    : {
        podcastSlug: slug,
        episodeId,
        start: ad.start,
        end: ad.end,
        sponsor: ad.sponsor ?? null,
        reason: ad.reason ?? null,
        confidence: ad.confidence,
        detectionStage: ad.detection_stage ?? null,
        patternId: ad.pattern_id ?? null,
        correctedBounds: null,
        category: (ad.category ?? null) as AdReviewItem['category'],
        actionApplied: ad.action_applied ?? null,
      };

  if (!internalCreateMode && detectedAds.length === 0) {
    return (
      <div className="bg-card rounded-lg border border-border p-6 text-center">
        <p className="text-muted-foreground mb-4">No ads detected on this episode.</p>
        <button
          type="button"
          className={`${ADD_BUTTON_BTN} ${focusRing}`}
          onClick={() => setInternalCreateMode(true)}
        >
          + Add new ad
        </button>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className={`ml-2 px-3 py-1.5 rounded-lg ${GHOST_BTN} text-sm ${focusRing}`}
          >
            Close
          </button>
        )}
      </div>
    );
  }

  // Advance to the next detected ad, or close the editor when this is the last
  // one. Shared by the save/submit and skip paths so their navigation stays in
  // sync.
  const advanceOrClose = () => {
    if (safeIndex < detectedAds.length - 1) {
      setSelectedAdIndex(safeIndex + 1);
    } else {
      onClose?.();
    }
  };

  const handleReviewSubmit = (s: AdReviewSubmit) => {
    if (s.kind === 'recategorize') {
      // Stays on this ad: the category is a property of the span, not a
      // verdict on it, so recategorizing is not a review decision.
      onCorrection({ type: 'recategorize', originalAd: ad, category: s.category });
      return;
    }
    if (s.kind === 'confirm') {
      onCorrection({ type: 'confirm', originalAd: ad, sponsor: s.sponsor });
    } else if (s.kind === 'reject') {
      onCorrection({ type: 'reject', originalAd: ad });
    } else {
      onCorrection({
        type: 'adjust',
        originalAd: ad,
        adjustedStart: s.adjustedStart,
        adjustedEnd: s.adjustedEnd,
        sponsor: s.sponsor,
      });
    }
    advanceOrClose();
  };

  // Exits create mode once a submission (single run, or the whole multi-span
  // batch) has gone through. Shared by the single-run path below (called
  // synchronously, matching the old behavior exactly) and onCreateDone,
  // which the modal calls once after every run in a multi-span submit saves.
  const finishCreate = () => {
    setInternalCreateMode(false);
    if (detectedAds.length === 0) onClose?.();
  };

  const handleCreateSubmit = (s: AdCreateSubmit, meta?: { silent?: boolean }): Promise<void> => {
    const correction: AdCorrection = {
      type: 'create',
      start: s.start,
      end: s.end,
      sponsor: s.sponsor,
      text_template: s.textTemplate,
      scope: s.scope,
      reason: s.reason,
      category: s.category,
    };
    // Single-run (not part of a multi-span batch) keeps the original
    // fire-and-forget onCorrection/mutate path unchanged. Only a multi-span
    // run (meta.silent) needs the awaitable path, to confirm or fail each
    // run before submitting the next.
    if (!meta?.silent) {
      onCorrection(correction);
      finishCreate();
      return Promise.resolve();
    }
    return onCorrectionAsync
      ? onCorrectionAsync(correction)
      : Promise.resolve(onCorrection(correction));
  };

  const handleSkip = advanceOrClose;

  const handleAddNew = () => {
    setInternalCreateMode(true);
  };

  const handleClose = () => {
    onClose?.();
  };

  // The key forces a clean remount whenever the mode flips or the
  // user switches between detected ads. Without this, the modal's
  // internal useState hooks (adStart, adEnd, peaks, wavesurfer ref,
  // etc.) retain values from the prior view and bleed across the
  // mode change, which manifested as "two stacked editors" in 2.2.5.
  // Include selectedAdIndex so the modal also remounts when the parent
  // navigates between ads via Jump, even if a future refactor changes
  // how `item` is derived. Cheap regression guard.
  const modalKey = internalCreateMode
    ? 'create'
    : `review-${safeIndex}-${item.start.toFixed(3)}-${item.end.toFixed(3)}`;

  return (
    <AdReviewModal
      key={modalKey}
      item={item}
      mode={internalCreateMode ? 'create' : 'review'}
      onClose={handleClose}
      onSubmit={handleReviewSubmit}
      onCreate={handleCreateSubmit}
      onCreateDone={finishCreate}
      onSkip={handleSkip}
      hasNext={safeIndex < detectedAds.length - 1}
      onAddNew={detectedAds.length > 0 && !internalCreateMode
        ? handleAddNew
        : undefined}
      audioMode={audioMode}
      onAudioModeChange={onAudioModeChange}
      hasOriginal={hasOriginal}
      processedAudioUrl={audioUrl}
      episodeDuration={audioDuration}
    />
  );
}

export default AdEditor;
