import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';

interface Podcasting20SectionProps {
  vttTranscriptsEnabled: boolean;
  chaptersEnabled: boolean;
  podpingEnabled: boolean;
  onVttTranscriptsEnabledChange: (enabled: boolean) => void;
  onChaptersEnabledChange: (enabled: boolean) => void;
  onPodpingEnabledChange: (enabled: boolean) => void;
}

function Podcasting20Section({
  vttTranscriptsEnabled,
  chaptersEnabled,
  podpingEnabled,
  onVttTranscriptsEnabledChange,
  onChaptersEnabledChange,
  onPodpingEnabledChange,
}: Podcasting20SectionProps) {
  return (
    <CollapsibleSection title="Podcasting 2.0">
      <div className="space-y-4">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={vttTranscriptsEnabled}
              onChange={onVttTranscriptsEnabledChange}
              ariaLabel="Generate VTT Transcripts"
            />
            <span className="text-sm font-medium text-foreground">Generate VTT Transcripts</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Create WebVTT transcripts with adjusted timestamps for podcast apps
          </p>
        </div>

        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={chaptersEnabled}
              onChange={onChaptersEnabledChange}
              ariaLabel="Generate Chapters"
            />
            <span className="text-sm font-medium text-foreground">Generate Chapters</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Create JSON chapters from ad boundaries and description timestamps
          </p>
        </div>

        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={podpingEnabled}
              onChange={onPodpingEnabledChange}
              ariaLabel="Podping notifications"
            />
            <span className="text-sm font-medium text-foreground">Podping notifications</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Listen for Podping publish notifications and refresh a feed as soon as its host announces a new episode. Only some hosts send Podping; feeds keep refreshing on the normal schedule either way.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default Podcasting20Section;
