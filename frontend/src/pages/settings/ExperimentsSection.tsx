import CollapsibleSection from '../../components/CollapsibleSection';
import { selectBase } from '../../components/fieldStyles';
import ExperimentalBadge from '../../components/ExperimentalBadge';

interface ExperimentsSectionProps {
  addressingMode: string;
  onAddressingModeChange: (v: string) => void;
}

function ExperimentsSection({ addressingMode, onAddressingModeChange }: ExperimentsSectionProps) {
  return (
    <CollapsibleSection
      title="Ad Addressing Mode"
      subtitle="Experimental: how the detector points at ads in the transcript."
    >
      <div>
        <div className="flex items-center gap-3 mb-2">
          <label htmlFor="adAddressingMode" className="text-sm font-medium text-foreground">
            Ad addressing mode
          </label>
          <ExperimentalBadge title="Experimental: segment IDs are still being benchmarked against timestamps" />
        </div>
        <select
          id="adAddressingMode"
          value={addressingMode}
          onChange={(e) => onAddressingModeChange(e.target.value)}
          className={`w-full ${selectBase}`}
        >
          <option value="timestamps">Timestamps (default)</option>
          <option value="segment_ids">Segment IDs</option>
          <option value="random">Random (A/B test)</option>
        </select>
        <p className="mt-2 text-sm text-muted-foreground">
          How the detector points at ads. Timestamps asks for start and end times; segment
          IDs asks it to name numbered transcript lines, which removes invented timestamps.
          Random alternates so the Stats page can compare them.
        </p>
      </div>
    </CollapsibleSection>
  );
}

export default ExperimentsSection;
