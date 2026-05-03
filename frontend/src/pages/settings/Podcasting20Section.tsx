import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';

interface Podcasting20SectionProps {
  vttTranscriptsEnabled: boolean;
  chaptersEnabled: boolean;
  onVttTranscriptsEnabledChange: (enabled: boolean) => void;
  onChaptersEnabledChange: (enabled: boolean) => void;
}

function Podcasting20Section({
  vttTranscriptsEnabled,
  chaptersEnabled,
  onVttTranscriptsEnabledChange,
  onChaptersEnabledChange,
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
      </div>
    </CollapsibleSection>
  );
}

export default Podcasting20Section;
