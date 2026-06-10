import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { clampNumericInput } from '../../utils/clampNumericInput';

export interface AudioCueState {
  enabled: boolean;
  freqMinHz: number;
  freqMaxHz: number;
  prominenceDb: number;
  minConfidence: number;
}

interface AudioCueDetectionSectionProps {
  audioCue: AudioCueState;
  onChange: (next: AudioCueState) => void;
}

type NumericKey = 'freqMinHz' | 'freqMaxHz' | 'prominenceDb' | 'minConfidence';

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
            Adds one extra ffmpeg pass per episode to find a recurring non-spoken cue. The cue never marks an ad on its own; the model must still find ad content in the transcript. It only sharpens an ad's start time.
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
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default AudioCueDetectionSection;
