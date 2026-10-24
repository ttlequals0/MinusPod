import CollapsibleSection from '../../components/CollapsibleSection';
import ExperimentalBadge from '../../components/ExperimentalBadge';
import { selectBase } from '../../components/fieldStyles';

interface ExperimentsSectionProps {
  addressingMode: string;
  onAddressingModeChange: (v: string) => void;
}

function ExperimentsSection({ addressingMode, onAddressingModeChange }: ExperimentsSectionProps) {
  return (
    <CollapsibleSection
      title="Ad addressing mode"
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
          aria-describedby="adAddressingMode-help"
        >
          <option value="timestamps">Timestamps (default)</option>
          <option value="segment_ids">Segment IDs</option>
          <option value="random">Random (A/B test)</option>
        </select>
        <p id="adAddressingMode-help" className="mt-2 text-sm text-muted-foreground">
          How the detector points at ads. Timestamps asks for start and end times; segment
          IDs asks it to name numbered transcript lines, which removes invented timestamps.
          Random alternates so the Stats page can compare them.
        </p>
      </div>
    </CollapsibleSection>
  );
}

export default ExperimentsSection;
