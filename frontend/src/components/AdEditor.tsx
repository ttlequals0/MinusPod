import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import AdReviewModal, { AdReviewItem, AdReviewSubmit, AdCreateSubmit } from './AdReviewModal';
import type { PatternScope } from '../api/patterns';

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
}

export interface AdCorrection {
  type: 'confirm' | 'reject' | 'adjust' | 'create';
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
}

interface AdEditorProps {
  detectedAds: DetectedAd[];
  audioDuration: number;
  audioUrl?: string;
  onCorrection: (correction: AdCorrection) => void;
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
  'px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-sm transition-colors hover:bg-primary/90';
const GHOST_BTN =
  'text-muted-foreground transition-colors hover:text-foreground hover:bg-accent';

export function AdEditor({
  detectedAds,
  audioDuration,
  audioUrl,
  onCorrection,
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
      };

  if (!internalCreateMode && detectedAds.length === 0) {
    return (
      <div className="bg-card rounded-lg border border-border p-6 text-center">
        <p className="text-muted-foreground mb-4">No ads detected on this episode.</p>
        <button
          type="button"
          className={ADD_BUTTON_BTN}
          onClick={() => setInternalCreateMode(true)}
        >
          + Add new ad
        </button>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className={`ml-2 px-3 py-1.5 rounded-lg ${GHOST_BTN} text-sm`}
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

  const handleCreateSubmit = (s: AdCreateSubmit) => {
    onCorrection({
      type: 'create',
      start: s.start,
      end: s.end,
      sponsor: s.sponsor,
      text_template: s.textTemplate,
      scope: s.scope,
      reason: s.reason,
    });
    setInternalCreateMode(false);
    if (detectedAds.length === 0) onClose?.();
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
