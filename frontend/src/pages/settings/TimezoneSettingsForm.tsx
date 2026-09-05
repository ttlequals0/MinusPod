import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useSyncFromQuery } from '../../hooks/useSyncFromQuery';
import { getNotificationTimezone, updateNotificationTimezone } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { btnPrimary } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';

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
    return supportedValuesOf ? supportedValuesOf('timeZone') : FALLBACK_ZONES;
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

  useSyncFromQuery(settings, (s) => setDraft(s.timezone));

  const saveMutation = useMutation({
    mutationFn: (timezone: string) => updateNotificationTimezone(timezone),
    onSuccess: (data) => queryClient.setQueryData(['notificationTimezone'], data),
  });

  if (isError) {
    return <p className="text-sm text-destructive">Failed to load the notification timezone.</p>;
  }
  if (isLoading || draft === null) {
    return <p className="text-sm text-muted-foreground">Loading timezone...</p>;
  }

  // isLoading/draft===null both cleared above, so a successful fetch has landed.
  const dirty = draft !== settings!.timezone;

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (draft) saveMutation.mutate(draft);
  }

  return (
    <form onSubmit={handleSave} className="space-y-3 p-4 rounded-lg border border-border bg-background">
      <p className="text-xs text-muted-foreground">
        Notifications also show times in this timezone, alongside UTC.
      </p>
      <div className="flex items-end gap-2 flex-wrap">
        <div className="flex-1 min-w-48">
          <label htmlFor="notification-timezone" className="block text-sm font-medium text-foreground mb-1">
            Timezone
          </label>
          <select
            id="notification-timezone"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className={`w-full ${selectBase}`}
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
          className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm ${focusRing}`}
        >
          {saveMutation.isPending ? 'Saving...' : 'Save timezone'}
        </button>
      </div>
      {saveMutation.isError && (
        <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
          {getErrorMessage(saveMutation.error, 'Failed to save timezone')}
        </div>
      )}
    </form>
  );
}

export default TimezoneSettingsForm;
