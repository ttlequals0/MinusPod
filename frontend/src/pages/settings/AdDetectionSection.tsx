import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import NumberInput from '../../components/NumberInput';

interface AdDetectionSectionProps {
  minCutConfidence: number;
  onMinCutConfidenceChange: (value: number) => void;
  minContentBetweenAdsSeconds: number;
  onMinContentBetweenAdsSecondsChange: (value: number) => void;
  verificationMissHoldMinConfidence: number;
  onVerificationMissHoldMinConfidenceChange: (value: number) => void;
  verificationMissAutocutMinConfidence: number;
  onVerificationMissAutocutMinConfidenceChange: (value: number) => void;
  learningMinConfidence: number;
  onLearningMinConfidenceChange: (value: number) => void;
  learningMinConfidenceLong: number;
  onLearningMinConfidenceLongChange: (value: number) => void;
  differentialMeasuredCorrMax: number;
  onDifferentialMeasuredCorrMaxChange: (value: number) => void;
  differentialHoldMinSeconds: number;
  onDifferentialHoldMinSecondsChange: (value: number) => void;
}

// Same shape as AudioCueDetectionSection's numRow, adapted to this section's
// flat (non-nested) props: a labeled NumberInput with a lo-hi range hint and
// a description paragraph underneath.
function numRow(
  value: number, onChange: (v: number) => void,
  id: string, label: string, lo: number, hi: number, step: number, fallback: number, hint: string,
) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-foreground mb-2">{label}</label>
      <div className="flex items-center gap-3">
        <NumberInput
          id={id}
          value={value}
          min={lo}
          max={hi}
          step={step}
          fallback={fallback}
          onCommit={onChange}
        />
        <span className="text-sm text-muted-foreground">{lo} to {hi}</span>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{hint}</p>
    </div>
  );
}

function AdDetectionSection({
  minCutConfidence,
  onMinCutConfidenceChange,
  minContentBetweenAdsSeconds,
  onMinContentBetweenAdsSecondsChange,
  verificationMissHoldMinConfidence,
  onVerificationMissHoldMinConfidenceChange,
  verificationMissAutocutMinConfidence,
  onVerificationMissAutocutMinConfidenceChange,
  learningMinConfidence,
  onLearningMinConfidenceChange,
  learningMinConfidenceLong,
  onLearningMinConfidenceLongChange,
  differentialMeasuredCorrMax,
  onDifferentialMeasuredCorrMaxChange,
  differentialHoldMinSeconds,
  onDifferentialHoldMinSecondsChange,
}: AdDetectionSectionProps) {
  return (
    <CollapsibleSection title="Ad Detection">
      <div className="space-y-6">
        <div>
          <label htmlFor="minCutConfidence" className="block text-sm font-medium text-foreground mb-2">
            Minimum Confidence Threshold: {Math.round(minCutConfidence * 100)}%
          </label>
          <input
            type="range"
            id="minCutConfidence"
            min="0.50"
            max="0.95"
            step="0.05"
            value={minCutConfidence}
            onChange={(e) => onMinCutConfidenceChange(parseFloat(e.target.value))}
            className="w-full h-2 bg-muted rounded-lg appearance-none cursor-pointer accent-primary"
          />
          <div className="flex justify-between text-xs text-muted-foreground mt-1">
            <span>More Aggressive (50%)</span>
            <span>More Conservative (95%)</span>
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            How confident the system must be before removing an ad.
            Lower values remove more potential ads but may include false positives.
          </p>
        </div>
        <div>
          <label htmlFor="minContentBetweenAdsSeconds" className="block text-sm font-medium text-foreground mb-2">
            Ad break filler gap threshold (s)
            {minContentBetweenAdsSeconds === 0 && (
              <span className="ml-2 text-xs text-muted-foreground font-normal">Disabled</span>
            )}
          </label>
          <input
            type="number"
            id="minContentBetweenAdsSeconds"
            min="0"
            max="60"
            step="1"
            value={minContentBetweenAdsSeconds}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              if (!isNaN(v) && v >= 0 && v <= 60) onMinContentBetweenAdsSecondsChange(v);
            }}
            className="w-32 px-3 py-1.5 text-sm bg-background border border-border rounded-md text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
          />
          <p className="mt-2 text-sm text-muted-foreground">
            Consecutive ads separated by less than this many seconds of speech content are merged into one cut. Set to 0 to disable.
          </p>
        </div>

        <div className="border border-border rounded-lg p-3 space-y-3">
          <div>
            <h4 className="text-sm font-semibold text-foreground">Verification pass</h4>
            <p className="text-xs text-muted-foreground mt-0.5">
              Ads pass 2 finds that pass 1 missed and that overlap no pass-1 marker.
            </p>
          </div>
          <div className="space-y-5">
            {numRow(
              verificationMissHoldMinConfidence, onVerificationMissHoldMinConfidenceChange,
              'verificationMissHoldMinConfidence', 'Hold floor', 0, 1, 0.05, 0.6,
              'A verification catch holds for review once it reaches this confidence. Below it, the catch is dropped.',
            )}

            <div>
              <label className="flex items-center gap-3 cursor-pointer">
                <ToggleSwitch
                  checked={verificationMissAutocutMinConfidence > 0}
                  onChange={(checked) => onVerificationMissAutocutMinConfidenceChange(checked ? 0.5 : 0)}
                  ariaLabel="Enable verification autocut"
                />
                <span className="text-sm font-medium text-foreground">Autocut</span>
              </label>
              <p className="mt-2 text-sm text-muted-foreground ml-14">
                Cut a verification catch automatically once it reaches a set confidence, instead of holding it for review. Off by default; catches then only ever hold or drop.
              </p>
              {verificationMissAutocutMinConfidence > 0 && (
                <div className="mt-3 ml-14">
                  {numRow(
                    verificationMissAutocutMinConfidence, onVerificationMissAutocutMinConfidenceChange,
                    'verificationMissAutocutMinConfidence', 'Autocut floor', 0.5, 1, 0.05, 0.5,
                    'Confidence a verification catch must reach to cut automatically.',
                  )}
                </div>
              )}
            </div>

            {numRow(
              learningMinConfidence, onLearningMinConfidenceChange,
              'learningMinConfidence', 'Pattern-learning floor', 0.5, 1, 0.05, 0.85,
              'Minimum confidence before a detection can teach the pattern matcher a new sponsor. Applies to ads up to 90 seconds long.',
            )}

            {numRow(
              learningMinConfidenceLong, onLearningMinConfidenceLongChange,
              'learningMinConfidenceLong', 'Pattern-learning floor, long ads', 0.5, 1, 0.05, 0.92,
              'Same floor for ads longer than 90 seconds. Higher by default, since a long span is costlier to learn wrong.',
            )}
          </div>
        </div>

        <div className="border border-border rounded-lg p-3 space-y-3">
          <div>
            <h4 className="text-sm font-semibold text-foreground">Differential detection</h4>
            <p className="text-xs text-muted-foreground mt-0.5">
              Compares two downloads of the same episode to find dynamically inserted ads.
            </p>
          </div>
          <div className="space-y-5">
            {numRow(
              differentialMeasuredCorrMax, onDifferentialMeasuredCorrMaxChange,
              'differentialMeasuredCorrMax', 'Correlation ceiling', 0, 1, 0.05, 0.6,
              'A cross-fetch region only becomes a detection candidate when its measured correlation is at or below this value. A higher correlation means the two fetches matched too closely to be a real ad swap.',
            )}

            <div>
              <label htmlFor="differentialHoldMinSeconds" className="block text-sm font-medium text-foreground mb-2">
                Hold minimum length (s)
                {differentialHoldMinSeconds === 0 && (
                  <span className="ml-2 text-xs text-muted-foreground font-normal">Disabled</span>
                )}
              </label>
              <div className="flex items-center gap-3">
                <NumberInput
                  id="differentialHoldMinSeconds"
                  value={differentialHoldMinSeconds}
                  min={0}
                  max={120}
                  step={1}
                  fallback={10}
                  onCommit={onDifferentialHoldMinSecondsChange}
                />
                <span className="text-sm text-muted-foreground">0 to 120</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                An uncorroborated differential candidate shorter than this is dropped instead of held for review. Set to 0 to hold a candidate of any length.
              </p>
            </div>
          </div>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default AdDetectionSection;
