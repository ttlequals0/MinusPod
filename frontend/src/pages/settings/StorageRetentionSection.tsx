import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';

interface StorageRetentionSectionProps {
  keepOriginalAudio: boolean;
  onKeepOriginalAudioChange: (enabled: boolean) => void;
  keepOriginalSaveIsPending: boolean;
  retentionEnabled: boolean;
  retentionDays: number;
  onRetentionEnabledChange: (enabled: boolean) => void;
  onRetentionDaysChange: (days: number) => void;
  originalRetentionDays: number;
  onOriginalRetentionDaysChange: (days: number) => void;
  onSave: () => void;
  saveIsPending: boolean;
  saveIsSuccess: boolean;
}

function StorageRetentionSection({
  keepOriginalAudio,
  onKeepOriginalAudioChange,
  keepOriginalSaveIsPending,
  retentionEnabled,
  retentionDays,
  onRetentionEnabledChange,
  onRetentionDaysChange,
  originalRetentionDays,
  onOriginalRetentionDaysChange,
  onSave,
  saveIsPending,
  saveIsSuccess,
}: StorageRetentionSectionProps) {
  const originalControlsActive = keepOriginalAudio && retentionEnabled;
  const originalExceedsProcessed =
    originalControlsActive && originalRetentionDays > retentionDays;

  const handleOriginalBlur = () => {
    if (originalRetentionDays > retentionDays) {
      onOriginalRetentionDaysChange(retentionDays);
    }
  };

  return (
    <CollapsibleSection title="Storage & Retention">
      <div className="space-y-4">
        <div>
          <div className="flex items-center gap-3 mb-3">
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={retentionEnabled}
                onChange={onRetentionEnabledChange}
                ariaLabel={retentionEnabled ? 'Retention enabled' : 'Retention disabled'}
              />
              <span className="text-sm font-medium text-foreground">
                {retentionEnabled ? 'Retention enabled' : 'Retention disabled'}
              </span>
            </label>
          </div>
          <div className="flex items-center gap-3">
            <label htmlFor="retentionDays" className="text-sm text-muted-foreground whitespace-nowrap">
              Retain processed files for:
            </label>
            <input
              type="number"
              id="retentionDays"
              value={retentionEnabled ? retentionDays : ''}
              onChange={(e) => onRetentionDaysChange(parseInt(e.target.value, 10) || 0)}
              disabled={!retentionEnabled}
              min={1}
              max={3650}
              placeholder="30"
              className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-50"
            />
            <span className="text-sm text-muted-foreground">days</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Processed audio files older than this will be deleted and episodes reset to Discovered. Episode records and processing history are always kept.
          </p>
        </div>

        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={keepOriginalAudio}
              onChange={onKeepOriginalAudioChange}
              disabled={keepOriginalSaveIsPending}
              ariaLabel="Keep original audio for ad boundary review"
            />
            <span className="text-sm font-medium text-foreground">
              Keep original audio for ad boundary review
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Retains the pre-cut audio file alongside the processed output so the ad editor can play what was removed. Roughly doubles per-episode audio storage. Only applies to new episodes processed after this is enabled.
          </p>

          <div className="mt-4 flex items-center gap-3">
            <label htmlFor="originalRetentionDays" className="text-sm text-muted-foreground whitespace-nowrap">
              Retain original audio for:
            </label>
            <input
              type="number"
              id="originalRetentionDays"
              value={originalControlsActive ? originalRetentionDays : ''}
              onChange={(e) => onOriginalRetentionDaysChange(parseInt(e.target.value, 10) || 0)}
              onBlur={handleOriginalBlur}
              disabled={!originalControlsActive}
              min={1}
              max={retentionEnabled ? retentionDays : 3650}
              placeholder={String(retentionDays)}
              aria-invalid={originalExceedsProcessed}
              className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring disabled:opacity-50"
            />
            <span className="text-sm text-muted-foreground">days</span>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">
            Drop the original copy sooner than the processed file. Capped at the processed retention ({retentionDays} {retentionDays === 1 ? 'day' : 'days'}). Saved with the button above.
          </p>
          {originalExceedsProcessed && (
            <p className="mt-1 text-sm text-destructive">
              Cannot exceed processed retention ({retentionDays} {retentionDays === 1 ? 'day' : 'days'}). Will clamp on save.
            </p>
          )}
        </div>

        <div className="pt-2">
          <button
            onClick={onSave}
            disabled={saveIsPending || originalExceedsProcessed}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm"
          >
            {saveIsPending ? 'Saving...' : 'Save Retention Settings'}
          </button>
          {saveIsSuccess && (
            <span className="ml-3 text-sm text-green-600 dark:text-green-400">Saved</span>
          )}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default StorageRetentionSection;
