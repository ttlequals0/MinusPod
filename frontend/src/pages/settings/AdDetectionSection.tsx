import CollapsibleSection from '../../components/CollapsibleSection';

interface AdDetectionSectionProps {
  minCutConfidence: number;
  onMinCutConfidenceChange: (value: number) => void;
}

function AdDetectionSection({
  minCutConfidence,
  onMinCutConfidenceChange,
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
            Controls how confident the system must be before removing an ad.
            Lower values remove more potential ads but may include false positives.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default AdDetectionSection;
