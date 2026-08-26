import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';

type SeedSponsorsKey =
  | 'seedSponsorsDetection'
  | 'seedSponsorsVerification'
  | 'seedSponsorsReviewer'
  | 'seedSponsorsResurrect';

interface SeedSponsorsSectionProps {
  detection: boolean;
  verification: boolean;
  reviewer: boolean;
  resurrect: boolean;
  onChange: (key: SeedSponsorsKey, value: boolean) => void;
}

function SeedSponsorsSection({
  detection,
  verification,
  reviewer,
  resurrect,
  onChange,
}: SeedSponsorsSectionProps) {
  return (
    <CollapsibleSection
      title="Seed sponsors"
      subtitle="Choose which prompts see the known-sponsor list. All on by default."
    >
      <div className="space-y-6">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={detection}
              onChange={(v) => onChange('seedSponsorsDetection', v)}
              ariaLabel="Detection"
            />
            <span className="text-sm font-medium text-foreground">Detection</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Include the known-sponsor list in the pass 1 detection prompt.
          </p>
        </div>

        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={verification}
              onChange={(v) => onChange('seedSponsorsVerification', v)}
              ariaLabel="Verification"
            />
            <span className="text-sm font-medium text-foreground">Verification</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Include the known-sponsor list in the pass 2 verification prompt.
          </p>
        </div>

        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={reviewer}
              onChange={(v) => onChange('seedSponsorsReviewer', v)}
              ariaLabel="Reviewer"
            />
            <span className="text-sm font-medium text-foreground">Reviewer</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Include the known-sponsor list when the reviewer checks each detection. Turning this off makes the reviewer an independent second opinion.
          </p>
        </div>

        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={resurrect}
              onChange={(v) => onChange('seedSponsorsResurrect', v)}
              ariaLabel="Resurrect"
            />
            <span className="text-sm font-medium text-foreground">Resurrect</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Include the known-sponsor list when the reviewer reconsiders rejected detections.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default SeedSponsorsSection;
