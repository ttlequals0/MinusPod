import CollapsibleSection from '../../components/CollapsibleSection';

interface ProcessingTimeoutsSectionProps {
  softTimeoutMinutes: number;
  hardTimeoutMinutes: number;
  softMinMinutes: number;
  hardMaxMinutes: number;
  onSoftChange: (minutes: number) => void;
  onHardChange: (minutes: number) => void;
  onSave: () => void;
  saveIsPending: boolean;
  saveIsSuccess: boolean;
  saveError: string | null;
}

function ProcessingTimeoutsSection({
  softTimeoutMinutes,
  hardTimeoutMinutes,
  softMinMinutes,
  hardMaxMinutes,
  onSoftChange,
  onHardChange,
  onSave,
  saveIsPending,
  saveIsSuccess,
  saveError,
}: ProcessingTimeoutsSectionProps) {
  return (
    <CollapsibleSection title="Processing Timeouts">
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <label htmlFor="softTimeoutMinutes" className="text-sm text-muted-foreground whitespace-nowrap w-36">
            Soft timeout:
          </label>
          <input
            type="number"
            id="softTimeoutMinutes"
            value={softTimeoutMinutes}
            onChange={(e) => onSoftChange(parseInt(e.target.value, 10) || 0)}
            min={softMinMinutes}
            max={hardMaxMinutes}
            className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <span className="text-sm text-muted-foreground">minutes</span>
        </div>
        <p className="text-sm text-muted-foreground -mt-2 ml-36 pl-3">
          After this, the job is considered stuck and cleared from the queue. Default 60.
        </p>

        <div className="flex items-center gap-3">
          <label htmlFor="hardTimeoutMinutes" className="text-sm text-muted-foreground whitespace-nowrap w-36">
            Hard timeout:
          </label>
          <input
            type="number"
            id="hardTimeoutMinutes"
            value={hardTimeoutMinutes}
            onChange={(e) => onHardChange(parseInt(e.target.value, 10) || 0)}
            min={softMinMinutes + 1}
            max={hardMaxMinutes}
            className="w-24 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <span className="text-sm text-muted-foreground">minutes</span>
        </div>
        <p className="text-sm text-muted-foreground -mt-2 ml-36 pl-3">
          Force-release the lock even when a worker still holds it. Must exceed soft timeout. Default 120.
        </p>

        <p className="text-sm text-muted-foreground">
          Raise these if long episodes on CPU or the largest Whisper model are being killed mid-run. Log entries will suggest an adjustment when a timeout fires.
        </p>

        <button
          onClick={onSave}
          disabled={saveIsPending}
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm"
        >
          {saveIsPending ? 'Saving...' : 'Save Timeouts'}
        </button>
        {saveIsSuccess && !saveError && (
          <span className="ml-3 text-sm text-green-600 dark:text-green-400">Saved</span>
        )}
        {saveError && (
          <span className="ml-3 text-sm text-red-600 dark:text-red-400">{saveError}</span>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default ProcessingTimeoutsSection;
