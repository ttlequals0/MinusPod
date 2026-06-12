import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';

interface PositionalPriorSectionProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}

function PositionalPriorSection({ enabled, onChange }: PositionalPriorSectionProps) {
  return (
    <CollapsibleSection
      title="Learned Ad Positions"
      subtitle="Learns where each show historically places ad breaks and uses it as a detection hint. Off by default."
    >
      <div>
        <label className="flex items-center gap-3 cursor-pointer">
          <ToggleSwitch
            checked={enabled}
            onChange={onChange}
            ariaLabel="Enable learned ad positions"
          />
          <span className="text-sm font-medium text-foreground">
            Enable learned ad positions
          </span>
        </label>
        <p className="mt-2 text-sm text-muted-foreground ml-14">
          Positions come from a feed's past cuts and your corrections; a feed needs at
          least 5 processed episodes before the hint kicks in. The hint never marks an
          ad on its own; the model must still find ad content in the transcript.
        </p>
      </div>
    </CollapsibleSection>
  );
}

export default PositionalPriorSection;
