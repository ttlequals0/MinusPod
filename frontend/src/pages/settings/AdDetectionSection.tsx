import CollapsibleSection from '../../components/CollapsibleSection';

interface AdDetectionSectionProps {
  minCutConfidence: number;
  onMinCutConfidenceChange: (value: number) => void;
  minContentBetweenAdsSeconds: number;
  onMinContentBetweenAdsSecondsChange: (value: number) => void;
}

function AdDetectionSection({
  minCutConfidence,
  onMinCutConfidenceChange,
  minContentBetweenAdsSeconds,
  onMinContentBetweenAdsSecondsChange,
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
      </div>
    </CollapsibleSection>
  );
}

export default AdDetectionSection;
