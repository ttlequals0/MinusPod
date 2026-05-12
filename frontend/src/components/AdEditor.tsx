import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import AdReviewModal, { AdReviewItem, AdReviewSubmit } from './AdReviewModal';
import { getSponsors } from '../api/sponsors';
import { getTranscriptSpan } from '../api/feeds';

// Save status for visual feedback
export type SaveStatus = 'idle' | 'saving' | 'success' | 'error';

export interface DetectedAd {
  start: number;
  end: number;
  confidence: number;
  reason: string;
  sponsor?: string;
  pattern_id?: number;
  detection_stage?: string;
  scope?: string;
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
  initialSeekTime?: number;
  saveStatus?: SaveStatus;
  selectedAdIndex?: number;
  onSelectedAdIndexChange?: (index: number) => void;
  // When true, the editor opens directly in 'create' mode for marking a
  // net-new ad on this episode (instead of reviewing detected ads).
  createMode?: boolean;
}

// Re-export for consumers
export type { AdReviewItem };

const ADD_BUTTON_BTN =
  'px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-sm transition-colors hover:bg-primary/90';
const GHOST_BTN =
  'text-muted-foreground transition-colors hover:text-foreground hover:bg-accent';
const PRIMARY_BTN =
  'bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50';

export function AdEditor({
  detectedAds,
  audioDuration,
  onCorrection,
  onClose,
  selectedAdIndex: externalSelectedAdIndex,
  onSelectedAdIndexChange,
  createMode = false,
}: AdEditorProps) {
  const { slug = '', episodeId = '' } = useParams<{ slug: string; episodeId: string }>();

  const [internalIndex, setInternalIndex] = useState(0);
  const selectedAdIndex = externalSelectedAdIndex ?? internalIndex;
  const setSelectedAdIndex = (i: number) => {
    if (onSelectedAdIndexChange) onSelectedAdIndexChange(i);
    else setInternalIndex(i);
  };

  // Initialized from the prop; toggled internally when the user clicks the
  // "+ Add new ad" button inside review mode. The parent always
  // remounts (showEditor false -> true) when it wants to force create
  // mode, so we don't need to mirror prop changes after mount.
  const [internalCreateMode, setInternalCreateMode] = useState(createMode);

  if (internalCreateMode) {
    return (
      <AdCreateForm
        slug={slug}
        episodeId={episodeId}
        duration={audioDuration}
        onSubmit={(c) => onCorrection(c)}
        onClose={() => {
          setInternalCreateMode(false);
          if (detectedAds.length === 0) onClose?.();
        }}
      />
    );
  }

  if (detectedAds.length === 0) {
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

  const safeIndex = Math.max(0, Math.min(selectedAdIndex, detectedAds.length - 1));
  const ad = detectedAds[safeIndex];
  const item: AdReviewItem = {
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

  const handleSubmit = (s: AdReviewSubmit) => {
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
    if (safeIndex < detectedAds.length - 1) {
      setSelectedAdIndex(safeIndex + 1);
    }
  };

  const handleSkip = () => {
    if (safeIndex < detectedAds.length - 1) {
      setSelectedAdIndex(safeIndex + 1);
    } else {
      onClose?.();
    }
  };

  return (
    <div className="space-y-3">
      {/* Ad selector + Add-new entry */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>
            Ad {safeIndex + 1} of {detectedAds.length}
          </span>
          <button
            type="button"
            disabled={safeIndex === 0}
            onClick={() => setSelectedAdIndex(safeIndex - 1)}
            className={`px-2 py-1 rounded ${GHOST_BTN} disabled:opacity-30`}
          >
            Prev
          </button>
          <button
            type="button"
            disabled={safeIndex >= detectedAds.length - 1}
            onClick={() => setSelectedAdIndex(safeIndex + 1)}
            className={`px-2 py-1 rounded ${GHOST_BTN} disabled:opacity-30`}
          >
            Next
          </button>
        </div>
        <button
          type="button"
          className={ADD_BUTTON_BTN}
          onClick={() => setInternalCreateMode(true)}
        >
          + Add new ad
        </button>
      </div>

      <AdReviewModal
        item={item}
        onClose={onClose ?? (() => {})}
        onSubmit={handleSubmit}
        onSkip={handleSkip}
        hasNext={safeIndex < detectedAds.length - 1}
      />
    </div>
  );
}

// ----------------------------------------------------------------------
// Create mode: simple controlled form with sponsor autocomplete and
// auto-populated text template from /transcript-span.

interface AdCreateFormProps {
  slug: string;
  episodeId: string;
  duration: number;
  onSubmit: (c: AdCorrection) => void;
  onClose: () => void;
}

interface SponsorOption {
  id: number;
  name: string;
}

function AdCreateForm({ slug, episodeId, duration, onSubmit, onClose }: AdCreateFormProps) {
  const initialEnd = Math.min(60, Math.max(0, duration));
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(initialEnd);
  const [sponsor, setSponsor] = useState('');
  const [sponsorOpts, setSponsorOpts] = useState<SponsorOption[]>([]);
  const [textTemplate, setTextTemplate] = useState('');
  const [scope, setScope] = useState<'podcast' | 'global'>('podcast');
  const [reason, setReason] = useState('');
  const [hintDismissed, setHintDismissed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSponsors()
      .then((list) =>
        setSponsorOpts(list.map((s: { id: number; name: string }) => ({ id: s.id, name: s.name })))
      )
      .catch(() => setSponsorOpts([]));
  }, []);

  // Auto-populate text template from /transcript-span when bounds change.
  // Only fills the field when empty so user edits aren't clobbered.
  useEffect(() => {
    if (start >= end) return;
    const t = setTimeout(() => {
      getTranscriptSpan(slug, episodeId, start, end)
        .then((res) => {
          setTextTemplate((prev) => (prev.length === 0 ? res.text : prev));
        })
        .catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [slug, episodeId, start, end]);

  const boundariesChanged = start !== 0 || end !== initialEnd;
  const showHint = !hintDismissed && !boundariesChanged;
  const trimmedTpl = textTemplate.trim();
  const canSubmit =
    !busy &&
    start >= 0 &&
    end > start &&
    end <= duration + 1 &&
    sponsor.trim().length > 0 &&
    trimmedTpl.length >= 50;

  const filteredSponsors = useMemo(() => {
    const q = sponsor.trim().toLowerCase();
    if (!q) return sponsorOpts.slice(0, 8);
    return sponsorOpts.filter((s) => s.name.toLowerCase().includes(q)).slice(0, 8);
  }, [sponsor, sponsorOpts]);

  const exactMatch = filteredSponsors.some(
    (s) => s.name.toLowerCase() === sponsor.trim().toLowerCase()
  );

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    try {
      onSubmit({
        type: 'create',
        start,
        end,
        sponsor: sponsor.trim(),
        text_template: trimmedTpl,
        scope,
        reason,
      });
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-card rounded-lg border border-border p-6 space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Add new ad</h2>
          <p className="text-sm text-muted-foreground">
            Mark an ad on this episode that the detector missed.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className={`p-1 rounded ${GHOST_BTN}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {showHint && (
        <div className="px-3 py-2 rounded-lg bg-secondary/40 border border-border text-sm text-muted-foreground">
          Enter the ad boundaries below. The text template auto-fills from the
          transcript on this episode.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm">
          <span className="block mb-1 text-muted-foreground">Start (seconds)</span>
          <input
            type="number"
            value={start}
            min={0}
            max={duration}
            step={0.1}
            onChange={(e) => {
              setHintDismissed(true);
              setStart(Number(e.target.value));
            }}
            className="w-full px-3 py-1.5 rounded border border-border bg-background text-foreground"
          />
        </label>
        <label className="text-sm">
          <span className="block mb-1 text-muted-foreground">End (seconds)</span>
          <input
            type="number"
            value={end}
            min={0}
            max={duration}
            step={0.1}
            onChange={(e) => {
              setHintDismissed(true);
              setEnd(Number(e.target.value));
            }}
            className="w-full px-3 py-1.5 rounded border border-border bg-background text-foreground"
          />
        </label>
      </div>

      <div className="text-sm text-muted-foreground tabular-nums">
        Duration: {(end - start).toFixed(1)}s
      </div>

      <label className="block text-sm">
        <span className="block mb-1 text-muted-foreground">Sponsor</span>
        <input
          type="text"
          value={sponsor}
          onChange={(e) => setSponsor(e.target.value)}
          placeholder="e.g. Squarespace, BetterHelp"
          className="w-full px-3 py-1.5 rounded border border-border bg-background text-foreground"
          list="sponsor-suggestions"
        />
        <datalist id="sponsor-suggestions">
          {filteredSponsors.map((s) => (
            <option key={s.id} value={s.name} />
          ))}
        </datalist>
        {sponsor.trim() && !exactMatch && (
          <div className="text-xs text-muted-foreground mt-1">
            + Add new sponsor: <span className="text-foreground">{sponsor.trim()}</span>
          </div>
        )}
      </label>

      <label className="block text-sm">
        <span className="block mb-1 text-muted-foreground">
          Text template (auto-populated from transcript; edit before submit)
        </span>
        <textarea
          value={textTemplate}
          onChange={(e) => setTextTemplate(e.target.value)}
          rows={5}
          className="w-full px-3 py-1.5 rounded border border-border bg-background text-foreground font-mono text-xs"
        />
        <div
          className={`text-xs mt-1 ${
            trimmedTpl.length < 50 ? 'text-destructive' : 'text-muted-foreground'
          }`}
        >
          {trimmedTpl.length} / 50 chars min
        </div>
      </label>

      <label className="block text-sm">
        <span className="block mb-1 text-muted-foreground">Reason (optional)</span>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why this is an ad"
          className="w-full px-3 py-1.5 rounded border border-border bg-background text-foreground"
        />
      </label>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={scope === 'global'}
          onChange={(e) => setScope(e.target.checked ? 'global' : 'podcast')}
        />
        <span>Apply across all podcasts (global pattern)</span>
      </label>

      <div className="flex items-center justify-end gap-2 pt-3 border-t border-border">
        <button
          type="button"
          onClick={onClose}
          className={`px-4 py-1.5 rounded-lg ${GHOST_BTN} text-sm`}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={!canSubmit}
          onClick={handleSubmit}
          className={`px-4 py-1.5 rounded-lg ${PRIMARY_BTN} text-sm`}
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}

export default AdEditor;
