import { useDraftField } from '../../hooks/useDraftField';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import ToggleSwitch from '../../components/ToggleSwitch';
import { getSettings, updateSettings } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { inputBase } from '../../components/fieldStyles';
import type { UpdateSettingsPayload } from '../../api/types';

const RETENTION_MIN = 1;
const RETENTION_MAX = 3650;

function DownloadHistorySettings() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: getSettings });

  const enabled = settings?.downloadHistoryEnabled?.value ?? true;
  const geoEnabled = settings?.downloadGeoLookupEnabled?.value ?? false;
  const retentionDays = settings?.downloadHistoryRetentionDays?.value ?? 90;

  const retention = useDraftField(settings, (s) => String(s?.downloadHistoryRetentionDays?.value ?? 90), (v) => String(Number(v)));

  const mutation = useMutation({
    mutationFn: (update: UpdateSettingsPayload) => updateSettings(update),
    onSuccess: async (_data, update) => {
      await queryClient.invalidateQueries({ queryKey: ['settings'] });
      if (update.downloadHistoryRetentionDays !== undefined) {
        retention.markClean(String(update.downloadHistoryRetentionDays));
      }
    },
  });

  const parsedRetention = Number(retention.value);
  const retentionValid = Number.isInteger(parsedRetention)
    && parsedRetention >= RETENTION_MIN && parsedRetention <= RETENTION_MAX;

  const saveRetention = () => {
    if (!retentionValid) return;
    if (parsedRetention === retentionDays) retention.markClean(String(retentionDays));
    else mutation.mutate({ downloadHistoryRetentionDays: parsedRetention });
  };

  return (
    <div className="pt-4 border-t border-border space-y-4">
      <div>
        <h3 className="text-base font-semibold text-foreground mb-1">Download history</h3>
        <p className="text-sm text-muted-foreground">
          Records each episode download with the client IP and app. Shown on the History page, Downloads tab.
        </p>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={enabled}
          onChange={(checked) => mutation.mutate({ downloadHistoryEnabled: checked })}
          disabled={mutation.isPending}
          ariaLabel="Record download history"
        />
        <span className="text-sm font-medium text-foreground">Record download history</span>
      </label>

      <div>
        <label className="flex items-center gap-3 cursor-pointer">
          <ToggleSwitch
            checked={geoEnabled}
            onChange={(checked) => mutation.mutate({ downloadGeoLookupEnabled: checked })}
            disabled={mutation.isPending}
            ariaLabel="Look up client locations"
          />
          <span className="text-sm font-medium text-foreground">Look up client locations</span>
        </label>
        <p className="mt-2 text-sm text-muted-foreground">
          Sends public client IPs to ipwho.is over HTTPS; off by default. Private and local addresses are never sent.
        </p>
      </div>

      <div>
        <label htmlFor="download-history-retention" className="block text-sm font-medium text-foreground mb-1">
          Keep history for (days)
        </label>
        <input
          id="download-history-retention"
          type="number"
          min={RETENTION_MIN}
          max={RETENTION_MAX}
          value={retention.value}
          onChange={(e) => retention.setValue(e.target.value)}
          onBlur={saveRetention}
          aria-invalid={!retentionValid}
          aria-describedby={[!retentionValid ? "download-retention-error" : "", mutation.isError ? "download-setting-error" : ""].filter(Boolean).join(" ") || undefined}
          className={`w-32 ${inputBase}`}
        />
        {!retentionValid && (
          <p id="download-retention-error" role="alert" className="mt-1 text-sm text-destructive">
            Enter a whole number from {RETENTION_MIN} to {RETENTION_MAX}.
          </p>
        )}
      </div>

      {mutation.isError && (
        <p id="download-setting-error" role="alert" className="text-sm text-destructive">
          {getErrorMessage(mutation.error, 'Failed to update setting')}
        </p>
      )}
    </div>
  );
}

export default DownloadHistorySettings;
