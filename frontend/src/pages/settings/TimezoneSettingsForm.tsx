import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { getNotificationTimezone, updateNotificationTimezone } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { btnLg, btnPrimary } from '../../components/buttonStyles';
import { selectBase, labelBase } from '../../components/fieldStyles';

const FALLBACK_ZONES = [
  'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Sao_Paulo', 'Europe/London', 'Europe/Berlin', 'Europe/Moscow',
  'Asia/Kolkata', 'Asia/Shanghai', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland',
];

// Full IANA list where the runtime supports it (most modern browsers); a
// short list otherwise. Avoids a TS lib bump for one optional API.
function listTimezones(): string[] {
  const supportedValuesOf = (Intl as { supportedValuesOf?: (key: string) => string[] })
    .supportedValuesOf;
  try {
    return supportedValuesOf ? ['UTC', ...supportedValuesOf('timeZone').filter((zone) => zone !== 'UTC')] : FALLBACK_ZONES;
  } catch {
    return FALLBACK_ZONES;
  }
}

function TimezoneSettingsForm() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const [zones] = useState(listTimezones);

  const { data: settings, isLoading, isError } = useQuery({
    queryKey: ['notificationTimezone'],
    queryFn: getNotificationTimezone,
  });

  useSyncFromQuery(settings, (s, previous) => {
    if (draft === null || draft === previous?.timezone) setDraft(s.timezone);
  });
  const dirty = draft !== null && !!settings && draft !== settings.timezone;
  useUnsavedChanges(dirty);

  const saveMutation = useMutation({
    mutationFn: (timezone: string) => updateNotificationTimezone(timezone),
    onSuccess: (data) => {
      queryClient.setQueryData(['notificationTimezone'], data);
      setDraft(data.timezone);
    },
  });

  if (isError) {
    return <p className="text-sm text-destructive">Could not load the notification timezone.</p>;
  }
  if (isLoading || draft === null) {
    return <p className="text-sm text-muted-foreground">Loading timezone...</p>;
  }

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (draft) saveMutation.mutate(draft);
  }

  return (
    <form onSubmit={handleSave} className="space-y-3 p-4 rounded-lg bg-muted/40">
      <p id="notification-timezone-help" className="text-xs text-muted-foreground">
        Notifications also show times in this timezone, alongside UTC.
      </p>
      <div className="flex items-end gap-2 flex-wrap">
        <div className="flex-1 min-w-48">
          <label htmlFor="notification-timezone" className={labelBase}>
            Timezone
          </label>
          <select
            id="notification-timezone"
            disabled={saveMutation.isPending}
            aria-describedby={`notification-timezone-help${saveMutation.isError ? " notification-timezone-error" : ""}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className={`max-sm:min-h-11 w-full ${selectBase}`}
          >
            {!zones.includes(draft) && <option value={draft}>{draft}</option>}
            {zones.map((zone) => (
              <option key={zone} value={zone}>{zone}</option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          disabled={saveMutation.isPending || !dirty}
          className={`${btnLg} ${btnPrimary}`}
        >
          {saveMutation.isPending ? 'Saving...' : 'Save timezone'}
        </button>
      </div>
      {saveMutation.isError && (
        <div id="notification-timezone-error" role="alert" className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
          {getErrorMessage(saveMutation.error, 'Could not save timezone.')}
        </div>
      )}
    </form>
  );
}

export default TimezoneSettingsForm;
