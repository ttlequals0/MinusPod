import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { clampNumericInput } from '../../utils/clampNumericInput';

export interface AudioCueState {
  enabled: boolean;
  freqMinHz: number;
  freqMaxHz: number;
  prominenceDb: number;
  minConfidence: number;
  templateScore: number;
  createFromPairs: boolean;
  snapConfidence: number;
  captureMinSeconds: number;
  captureMaxSeconds: number;
  captureMaxIntroSeconds: number;
  captureMaxOutroSeconds: number;
  pairConfidence: number;
  pairMinBreakSeconds: number;
  pairMaxBreakSeconds: number;
}

interface AudioCueDetectionSectionProps {
  audioCue: AudioCueState;
  onChange: (next: AudioCueState) => void;
}

type NumericKey =
  | 'freqMinHz' | 'freqMaxHz' | 'prominenceDb' | 'minConfidence' | 'templateScore'
  | 'snapConfidence' | 'captureMinSeconds' | 'captureMaxSeconds'
  | 'captureMaxIntroSeconds' | 'captureMaxOutroSeconds'
  | 'pairConfidence' | 'pairMinBreakSeconds' | 'pairMaxBreakSeconds';

const inputClass =
  'w-28 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground ' +
  'focus:outline-hidden focus:ring-2 focus:ring-ring';

function AudioCueDetectionSection({ audioCue, onChange }: AudioCueDetectionSectionProps) {
  const update = <K extends keyof AudioCueState>(key: K, value: AudioCueState[K]) =>
    onChange({ ...audioCue, [key]: value });

  const numUpdate = (
    key: NumericKey,
    raw: string,
    lo: number,
    hi: number,
    fallback: number,
    parse: (s: string) => number,
  ) => {
    const v = clampNumericInput(raw, lo, hi, fallback, parse);
    if (v !== undefined) update(key, v);
  };

  const numRow = (
    key: NumericKey, id: string, label: string,
    lo: number, hi: number, step: number, fallback: number, hint: string,
    parse: (s: string) => number = parseFloat,
  ) => (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-foreground mb-2">{label}</label>
      <div className="flex items-center gap-3">
        <input
          type="number"
          id={id}
          value={audioCue[key]}
          min={lo}
          max={hi}
          step={step}
          onChange={(e) => numUpdate(key, e.target.value, lo, hi, fallback, parse)}
          className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
        />
        <span className="text-sm text-muted-foreground">{lo} to {hi}</span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{hint}</p>
    </div>
  );

  return (
    <CollapsibleSection
      title="Audio Cue Detection"
      subtitle="Detects a short ding/stinger some shows play before an ad break and feeds it to the detector as a timing hint. Off by default."
    >
      <div className="space-y-6">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={audioCue.enabled}
              onChange={(v) => update('enabled', v)}
              ariaLabel="Enable audio cue detection"
            />
            <span className="text-sm font-medium text-foreground">
              Enable audio cue detection
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Adds one ffmpeg pass per episode to find a recurring non-spoken cue. The cue never marks an ad on its own - it only sharpens the boundary of an ad the model finds in the transcript.
          </p>
        </div>

        {audioCue.enabled && (
          <div className="space-y-6 pt-2">
            <div>
              <span className="block text-sm font-medium text-foreground mb-2">Frequency band</span>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  aria-label="Band low edge in Hz"
                  value={audioCue.freqMinHz}
                  onChange={(e) => numUpdate('freqMinHz', e.target.value, 20, 20000, 1500, (s) => parseInt(s, 10))}
                  min={20}
                  max={20000}
                  step={50}
                  className={inputClass}
                />
                <span className="text-sm text-muted-foreground">to</span>
                <input
                  type="number"
                  aria-label="Band high edge in Hz"
                  value={audioCue.freqMaxHz}
                  onChange={(e) => numUpdate('freqMaxHz', e.target.value, 20, 20000, 8000, (s) => parseInt(s, 10))}
                  min={20}
                  max={20000}
                  step={50}
                  className={inputClass}
                />
                <span className="text-sm text-muted-foreground">Hz</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                The band the cue lives in. Chimes and bells usually sit between roughly 1.5 and 8 kHz; widen or shift it if your show's cue is lower or higher. The low edge must be below the high edge.
              </p>
            </div>

            <div>
              <label htmlFor="audioCueProminence" className="block text-sm font-medium text-foreground mb-2">
                Prominence threshold
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  id="audioCueProminence"
                  value={audioCue.prominenceDb}
                  onChange={(e) => numUpdate('prominenceDb', e.target.value, 1, 40, 9, parseFloat)}
                  min={1}
                  max={40}
                  step={0.5}
                  className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
                />
                <span className="text-sm text-muted-foreground">dB above baseline (1-40)</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                How far above the in-band speech baseline a sound must rise to count as a cue. Lower catches quieter cues but adds false positives.
              </p>
            </div>

            <div>
              <label htmlFor="audioCueMinConfidence" className="block text-sm font-medium text-foreground mb-2">
                Minimum confidence
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  id="audioCueMinConfidence"
                  value={audioCue.minConfidence}
                  onChange={(e) => numUpdate('minConfidence', e.target.value, 0, 1, 0.8, parseFloat)}
                  min={0}
                  max={1}
                  step={0.05}
                  className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
                />
                <span className="text-sm text-muted-foreground">0-1</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                Drop cues weaker than this. The model is never shown a cue below 0.80 confidence regardless of this value.
              </p>
            </div>

            <div>
              <label htmlFor="audioCueTemplateScore" className="block text-sm font-medium text-foreground mb-2">
                Template match score
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  id="audioCueTemplateScore"
                  value={audioCue.templateScore}
                  onChange={(e) => numUpdate('templateScore', e.target.value, 0, 0.99, 0.75, parseFloat)}
                  min={0}
                  max={0.99}
                  step={0.05}
                  className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
                />
                <span className="text-sm text-muted-foreground">0-0.99</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                Match score a marked cue must reach to register on another episode. Lower catches more but risks false matches. Applies only to feeds with templates; otherwise the spectral knobs above are used.
              </p>
            </div>

            <div className="border-t border-border pt-4 space-y-5">
              <span className="block text-sm font-medium text-foreground">Advanced tuning</span>
              {numRow('snapConfidence', 'audioCueSnapConfidence', 'Snap confidence floor', 0, 1, 0.05, 0.8,
                'Minimum cue confidence before a cue may move an ad edge. Higher is stricter.')}
              {numRow('captureMinSeconds', 'audioCueCaptureMinSeconds', 'Capture minimum length (s)', 0.05, 10, 0.05, 0.2,
                'Shortest cue you may bracket; a floor that keeps very short sounds from matching everything.')}
              {numRow('captureMaxSeconds', 'audioCueCaptureMaxSeconds', 'Capture maximum length (s)', 0.05, 30, 0.5, 10,
                'Longest cue you may bracket.')}
              {numRow('captureMaxIntroSeconds', 'audioCueCaptureMaxIntroSeconds', 'Show-intro capture maximum (s)', 0.05, 120, 1, 60,
                'Longest show-intro stinger you may bracket. Intros run longer than ad-break dings.')}
              {numRow('captureMaxOutroSeconds', 'audioCueCaptureMaxOutroSeconds', 'Show-outro capture maximum (s)', 0.05, 120, 1, 60,
                'Longest show-outro stinger you may bracket. Outros run longer than ad-break dings.')}
              {numRow('pairConfidence', 'audioCuePairConfidence', 'Cue-pair confidence floor', 0, 1, 0.05, 0.85,
                'Minimum cue confidence to synthesize an ad from a cue pair. Higher than the snap floor because this creates an ad rather than refining one.')}
              {numRow('pairMinBreakSeconds', 'audioCuePairMinBreakSeconds', 'Cue-pair minimum break (s)', 1, 600, 5, 30,
                'Shortest span between two cues that may form a synthesized ad.')}
              {numRow('pairMaxBreakSeconds', 'audioCuePairMaxBreakSeconds', 'Cue-pair maximum break (s)', 1, 3600, 30, 480,
                'Longest span between two cues that may form a synthesized ad.')}
            </div>
          </div>
        )}

        <div className="border-t border-border pt-4">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={audioCue.createFromPairs}
              onChange={(v) => update('createFromPairs', v)}
              ariaLabel="Create ads from cue pairs"
            />
            <span className="text-sm font-medium text-foreground">
              Create ads from cue pairs when the LLM misses a break
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            When two high-confidence cues bracket a break the model missed, create a cue-only ad for the reviewer to check. Off by default - turn it on once you trust the matcher on this feed.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default AudioCueDetectionSection;
