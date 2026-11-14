import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../../api/client';
import {
  getProcessingSchedule,
  updateProcessingSchedule,
  type ProcessingScheduleSettings,
  type ScheduleDay,
} from '../../api/settings';
import { btnBase, btnLg, btnPrimary, btnSecondary } from '../../components/buttonStyles';
import { DemoDisabled } from '../../components/DemoDisabled';
import { focusRing, inputBase, labelBase } from '../../components/fieldStyles';
import { SkeletonRows } from '../../components/Skeleton';
import ToggleSwitch from '../../components/ToggleSwitch';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { formatDateTime } from '../../utils/format';
import SavedBadge from './SavedBadge';

const DAYS: ReadonlyArray<{ value: ScheduleDay; name: string }> = [
  { value: 'mon', name: 'Monday' },
  { value: 'tue', name: 'Tuesday' },
  { value: 'wed', name: 'Wednesday' },
  { value: 'thu', name: 'Thursday' },
  { value: 'fri', name: 'Friday' },
  { value: 'sat', name: 'Saturday' },
  { value: 'sun', name: 'Sunday' },
];

interface Draft {
  enabled?: boolean;
  start?: string;
  end?: string;
  days?: ScheduleDay[];
}

// Status of the saved window, never the draft.
function scheduleStatus(data: ProcessingScheduleSettings): string | null {
  if (!data.enabled) return null;
  const when = data.nextChangeAt ? formatDateTime(data.nextChangeAt) : null;
  if (data.open) return when ? `Open now. Closes ${when}.` : 'Open now. The window does not close.';
  return when ? `Closed now. Opens ${when}.` : 'Closed now.';
}

function ProcessingScheduleBlock({ active }: { active: boolean }) {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['processingSchedule'],
    queryFn: getProcessingSchedule,
    enabled: active,
  });
  const [draft, setDraft] = useState<Draft>({});

  const enabled = draft.enabled ?? data?.enabled ?? false;
  const start = draft.start ?? data?.start ?? '';
  const end = draft.end ?? data?.end ?? '';
  const days = draft.days ?? data?.days ?? [];
  const noDays = days.length === 0;
  const noTimes = !start || !end;
  useUnsavedChanges(!!data && (enabled !== data.enabled || start !== data.start || end !== data.end
    || days.join() !== data.days.join()));

  const save = useMutation({
    mutationFn: () => updateProcessingSchedule({ enabled, start, end, days }),
    onSuccess: () => {
      setDraft({});
      qc.invalidateQueries({ queryKey: ['processingSchedule'] });
      qc.invalidateQueries({ queryKey: ['processing-status'] });
    },
  });

  if (isLoading || !active) return <SkeletonRows count={3} />;
  if (isError || !data) {
    // A failed GET must not render the form from defaults; Save would overwrite the stored schedule.
    return (
      <div className="space-y-2">
        <p className="text-sm text-destructive">Could not load the processing schedule.</p>
        <button type="button" onClick={() => refetch()} className={`${btnBase} ${btnSecondary}`}>Retry</button>
      </div>
    );
  }

  const toggleDay = (day: ScheduleDay) => {
    const next = days.includes(day) ? days.filter((d) => d !== day) : [...days, day];
    setDraft((d) => ({ ...d, days: DAYS.map((x) => x.value).filter((v) => next.includes(v)) }));
  };
  const status = scheduleStatus(data);

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={enabled}
          onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
          ariaLabel="Processing schedule toggle"
        />
        <span className="text-sm font-medium text-foreground">Process on a schedule</span>
      </label>
      <p className="text-sm text-muted-foreground -mt-2">
        Only start processing during a weekly window. Episodes already processing finish.
        Everything else, including Play and Reprocess, waits in the queue for the next window.
      </p>

      <div className="grid grid-cols-[repeat(2,minmax(0,8rem))] gap-3">
        {([
          { key: 'start', id: 'schedule-start', label: 'From', value: start },
          { key: 'end', id: 'schedule-end', label: 'Until', value: end },
        ] as const).map((f) => (
          <div key={f.key} className="min-w-0">
            <label htmlFor={f.id} className={labelBase}>{f.label}</label>
            <input
              id={f.id}
              type="time"
              value={f.value}
              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
              className={`${inputBase} w-full min-w-0 max-sm:px-2 [&::-webkit-datetime-edit]:min-w-0 [&::-webkit-datetime-edit]:p-0`}
              aria-describedby="schedule-help"
              aria-invalid={!f.value || undefined}
            />
          </div>
        ))}
      </div>
      <p id="schedule-help" className="text-xs text-muted-foreground -mt-2">
        Times use the instance time zone ({data.timezone}). An Until time earlier than From
        runs past midnight into the next day. The same time for both is a full 24 hours.
      </p>

      <div role="group" aria-labelledby="schedule-days-label" aria-describedby={noDays ? 'schedule-days-error' : undefined}>
        <p id="schedule-days-label" className={labelBase}>Days the window starts on</p>
        <div className="flex flex-wrap gap-2">
          {DAYS.map((day) => {
            const selected = days.includes(day.value);
            return (
              <button
                key={day.value}
                type="button"
                aria-pressed={selected}
                aria-label={day.name}
                onClick={() => toggleDay(day.value)}
                className={`inline-flex items-center justify-center min-h-10 min-w-12 px-3 rounded border text-sm transition-colors ${focusRing} ${
                  selected
                    ? 'border-primary bg-primary/10 text-primary font-medium'
                    : 'border-input bg-background text-foreground hover:bg-accent'
                }`}
              >
                {day.name.slice(0, 3)}
              </button>
            );
          })}
        </div>
        {noDays && (
          <p id="schedule-days-error" role="alert" className="mt-2 text-sm text-destructive">
            Select at least one day.
          </p>
        )}
      </div>

      {status && <p className="text-sm text-foreground" role="status">{status}</p>}
      {save.isError && (
        <p className="text-sm text-destructive">{getErrorMessage(save.error, 'Could not save the processing schedule.')}</p>
      )}

      <div className="flex items-center gap-2">
        <DemoDisabled>
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending || noDays || noTimes}
            className={`${btnLg} ${btnPrimary}`}
          >
            {save.isPending ? 'Saving...' : 'Save'}
          </button>
        </DemoDisabled>
        {save.isSuccess && <SavedBadge className="ml-1" />}
      </div>
    </div>
  );
}

export default ProcessingScheduleBlock;
