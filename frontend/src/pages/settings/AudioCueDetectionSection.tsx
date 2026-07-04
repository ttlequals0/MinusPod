import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import NumberInput from '../../components/NumberInput';

export interface AudioCueState {
  enabled: boolean;
  freqMinHz: number;
  freqMaxHz: number;
  prominenceDb: number;
  minConfidence: number;
  templateScore: number;
  formantAttenDb: number;
  createFromPairs: boolean;
  snapConfidence: number;
  snapLeadSeconds: number;
  snapLagSeconds: number;
  captureMinSeconds: number;
  captureMaxSeconds: number;
  captureMaxIntroSeconds: number;
  captureMaxOutroSeconds: number;
  pairConfidence: number;
  pairMinBreakSeconds: number;
  pairMaxBreakSeconds: number;
  pairMaxBreakFraction: number;
  silenceSnapNoiseDb: number;
  silenceSnapMinDurationSeconds: number;
  silenceSnapMaxDistanceSeconds: number;
}

interface AudioCueDetectionSectionProps {
  audioCue: AudioCueState;
  onChange: (next: AudioCueState) => void;
}

type NumericKey =
  | 'freqMinHz' | 'freqMaxHz' | 'prominenceDb' | 'minConfidence' | 'templateScore'
  | 'formantAttenDb'
  | 'snapConfidence' | 'snapLeadSeconds' | 'snapLagSeconds'
  | 'captureMinSeconds' | 'captureMaxSeconds'
  | 'captureMaxIntroSeconds' | 'captureMaxOutroSeconds'
  | 'pairConfidence' | 'pairMinBreakSeconds' | 'pairMaxBreakSeconds'
  | 'pairMaxBreakFraction'
  | 'silenceSnapNoiseDb' | 'silenceSnapMinDurationSeconds'
  | 'silenceSnapMaxDistanceSeconds';

const inputClass =
  'w-28 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground ' +
  'focus:outline-hidden focus:ring-2 focus:ring-ring';

function AudioCueDetectionSection({ audioCue, onChange }: AudioCueDetectionSectionProps) {
  const update = <K extends keyof AudioCueState>(key: K, value: AudioCueState[K]) =>
    onChange({ ...audioCue, [key]: value });

  const numRow = (
    key: NumericKey, id: string, label: string,
    lo: number, hi: number, step: number, fallback: number, hint: string,
    parse: (s: string) => number = parseFloat,
  ) => (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-foreground mb-2">{label}</label>
      <div className="flex items-center gap-3">
        <NumberInput
          id={id}
          value={audioCue[key]}
          min={lo}
          max={hi}
          step={step}
          fallback={fallback}
          parse={parse}
          onCommit={(v) => update(key, v)}
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
          <div className="space-y-3 pt-2">
            <div className="border border-border rounded-lg p-3 space-y-3">
              <div>
                <h4 className="text-sm font-semibold text-foreground">Finding cues</h4>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Spots candidate cues in the audio and brackets how long a cue may run.
                </p>
              </div>
              <div className="space-y-5">
                <div>
                  <span className="block text-sm font-medium text-foreground mb-2">Frequency band</span>
                  <div className="flex items-center gap-3">
                    <NumberInput
                      ariaLabel="Band low edge in Hz"
                      value={audioCue.freqMinHz}
                      min={20}
                      max={20000}
                      step={50}
                      fallback={1500}
                      parse={(s) => parseInt(s, 10)}
                      className={inputClass}
                      onCommit={(v) => update('freqMinHz', v)}
                    />
                    <span className="text-sm text-muted-foreground">to</span>
                    <NumberInput
                      ariaLabel="Band high edge in Hz"
                      value={audioCue.freqMaxHz}
                      min={20}
                      max={20000}
                      step={50}
                      fallback={8000}
                      parse={(s) => parseInt(s, 10)}
                      className={inputClass}
                      onCommit={(v) => update('freqMaxHz', v)}
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
                    <NumberInput
                      id="audioCueProminence"
                      value={audioCue.prominenceDb}
                      min={1}
                      max={40}
                      step={0.5}
                      fallback={9}
                      onCommit={(v) => update('prominenceDb', v)}
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
                    <NumberInput
                      id="audioCueMinConfidence"
                      value={audioCue.minConfidence}
                      min={0}
                      max={1}
                      step={0.05}
                      fallback={0.8}
                      onCommit={(v) => update('minConfidence', v)}
                    />
                    <span className="text-sm text-muted-foreground">0-1</span>
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Drop cues weaker than this. The model is never shown a cue below 0.80 confidence regardless of this value.
                  </p>
                </div>

                {numRow('captureMinSeconds', 'audioCueCaptureMinSeconds', 'Capture minimum length (s)', 0.05, 10, 0.05, 0.2,
                  'Shortest cue you may bracket; a floor that keeps very short sounds from matching everything.')}
                {numRow('captureMaxSeconds', 'audioCueCaptureMaxSeconds', 'Capture maximum length (s)', 0.05, 30, 0.5, 10,
                  'Longest cue you may bracket.')}
                {numRow('captureMaxIntroSeconds', 'audioCueCaptureMaxIntroSeconds', 'Show-intro capture maximum (s)', 0.05, 120, 1, 60,
                  'Longest show-intro stinger you may bracket. Intros run longer than ad-break dings.')}
                {numRow('captureMaxOutroSeconds', 'audioCueCaptureMaxOutroSeconds', 'Show-outro capture maximum (s)', 0.05, 120, 1, 60,
                  'Longest show-outro stinger you may bracket. Outros run longer than ad-break dings.')}
              </div>
            </div>

            <div className="border border-border rounded-lg p-3 space-y-3">
              <div>
                <h4 className="text-sm font-semibold text-foreground">Matching templates</h4>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Scores saved cue templates against new episodes. Minimum confidence (above) and template match score also gate which cues reach ad cutting.
                </p>
              </div>
              <div className="space-y-5">
                <div>
                  <label htmlFor="audioCueTemplateScore" className="block text-sm font-medium text-foreground mb-2">
                    Template match score
                  </label>
                  <div className="flex items-center gap-3">
                    <NumberInput
                      id="audioCueTemplateScore"
                      value={audioCue.templateScore}
                      min={0}
                      max={0.99}
                      step={0.05}
                      fallback={0.75}
                      onCommit={(v) => update('templateScore', v)}
                    />
                    <span className="text-sm text-muted-foreground">0-0.99</span>
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Match score a marked cue must reach to register on another episode. Lower catches more but risks false matches. Applies only to feeds with templates; otherwise the spectral knobs above are used. A cue must reach 0.80 confidence to affect a cut (the model, snap, and pairing floors); a lower value here only surfaces weaker cues in diagnostics.
                  </p>
                </div>

                {numRow('formantAttenDb', 'audioCueFormantAttenDb', 'Voiceover attenuation (dB)', 0, 24, 1, 0,
                  'When a saved cue is music under a voiceover that varies per episode, attenuate the 800-3400 Hz speech band so matching keys on the constant bed. 0 = off. Only that band is touched, so bass beds and high chimes are unaffected.')}
              </div>
            </div>

            <div className="border border-border rounded-lg p-3 space-y-3">
              <div>
                <h4 className="text-sm font-semibold text-foreground">Ad cutting</h4>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Uses accepted cues to snap ad edges or build ads from cue pairs.
                </p>
              </div>
              <div className="space-y-5">
                {numRow('snapConfidence', 'audioCueSnapConfidence', 'Snap confidence floor', 0, 1, 0.05, 0.8,
                  'Minimum cue confidence before a cue may move an ad edge. Higher is stricter.')}
                {numRow('snapLeadSeconds', 'audioCueSnapLeadSeconds', 'Snap lead window (s)', 0.5, 30, 0.5, 10,
                  'How far before an ad edge a cue may sit and still snap the boundary. Wider catches cues that land earlier than the LLM mark.')}
                {numRow('snapLagSeconds', 'audioCueSnapLagSeconds', 'Snap lag window (s)', 0.5, 30, 0.5, 4,
                  'How far after an ad edge a cue may sit and still snap the boundary. Covers cases where the LLM mark precedes the cue.')}
                {numRow('silenceSnapNoiseDb', 'silenceSnapNoiseDb', 'Silence threshold (dBFS)', -90, -20, 1, -50,
                  'Audio quieter than this counts as silence for silence snap. Applies only on feeds with the per-feed opt-in enabled.')}
                {numRow('silenceSnapMinDurationSeconds', 'silenceSnapMinDurationSeconds', 'Silence minimum duration (s)', 0.1, 5, 0.1, 0.3,
                  'Shortest quiet span that counts as a silence.')}
                {numRow('silenceSnapMaxDistanceSeconds', 'silenceSnapMaxDistanceSeconds', 'Silence snap max distance (s)', 0.25, 10, 0.25, 2,
                  'Farthest an ad edge may move to reach a detected silence.')}
                {numRow('pairConfidence', 'audioCuePairConfidence', 'Cue-pair confidence floor', 0, 1, 0.05, 0.85,
                  'Minimum cue confidence to synthesize an ad from a cue pair. Higher than the snap floor because this creates an ad rather than refining one.')}
                {numRow('pairMinBreakSeconds', 'audioCuePairMinBreakSeconds', 'Cue-pair minimum break (s)', 1, 600, 5, 30,
                  'Shortest span between two cues that may form a synthesized ad.')}
                {numRow('pairMaxBreakSeconds', 'audioCuePairMaxBreakSeconds', 'Cue-pair maximum break (s)', 1, 3600, 30, 480,
                  'Longest span between two cues that may form a synthesized ad.')}
                {numRow('pairMaxBreakFraction', 'audioCuePairMaxBreakFraction', 'Cue-pair maximum break (fraction of episode)', 0, 1, 0.05, 0.5,
                  'Reject a cue pair spanning more than this fraction of the episode. A short-episode backstop against a pair bracketing most of the show. 0 disables it.')}

                <div>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <ToggleSwitch
                      checked={audioCue.createFromPairs}
                      onChange={(v) => update('createFromPairs', v)}
                      ariaLabel="Create ads from cue pairs"
                    />
                    <span className="text-sm font-medium text-foreground">
                      Create ads from cue pairs
                    </span>
                  </label>
                  <p className="mt-2 text-sm text-muted-foreground ml-14">
                    When two high-confidence cues bracket a break the model missed, create a cue-only ad for the reviewer to check. Off by default - turn it on once you trust the matcher on this feed.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default AudioCueDetectionSection;
