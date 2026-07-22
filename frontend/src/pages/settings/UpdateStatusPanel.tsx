import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getUpdateCheckSettings, getUpdateStatus, updateUpdateCheckSettings } from '../../api/updates';
import type { UpdateCheckSettings } from '../../api/types';
import ToggleSwitch from '../../components/ToggleSwitch';
import { btnSecondary } from '../../components/buttonStyles';

export default function UpdateStatusPanel() {
  const queryClient = useQueryClient();
  const [checking, setChecking] = useState(false);
  const { data: status } = useQuery({
    queryKey: ['update-status'],
    queryFn: () => getUpdateStatus(),
    staleTime: 5 * 60 * 1000,
  });
  const { data: settings } = useQuery({
    queryKey: ['update-check-settings'],
    queryFn: getUpdateCheckSettings,
  });
  const settingsMutation = useMutation({
    mutationFn: (payload: Partial<UpdateCheckSettings>) => updateUpdateCheckSettings(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['update-check-settings'], data);
      queryClient.invalidateQueries({ queryKey: ['update-status'] });
    },
  });

  const checkNow = async () => {
    setChecking(true);
    try {
      const fresh = await getUpdateStatus(true);
      queryClient.setQueryData(['update-status'], fresh);
    } finally {
      setChecking(false);
    }
  };

  if (!status || !settings) return null;

  const target = status.channel === 'stable' ? status.stable : status.edge;

  return (
    <div className="mt-4 border-t border-border pt-4 space-y-3">
      <p className="text-sm text-muted-foreground">
        Running {status.current.version}
        {status.current.releaseDate ? ` (released ${status.current.releaseDate})` : ''}
      </p>
      <p className="text-sm text-foreground">
        {status.updateAvailable && target
          ? `${target.version} is available on the ${status.channel} channel.`
          : 'Up to date on the selected channel.'}
      </p>
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-sm text-foreground">
          Channel
          <select
            aria-label="Channel"
            className="rounded border border-input bg-background px-2 py-1 text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
            value={settings.channel}
            onChange={(e) => settingsMutation.mutate({ channel: e.target.value as 'stable' | 'edge' })}
          >
            <option value="stable">Stable</option>
            <option value="edge">Edge</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
          <ToggleSwitch
            checked={settings.enabled}
            onChange={(v: boolean) => settingsMutation.mutate({ enabled: v })}
            ariaLabel="Check for updates daily"
          />
          Check for updates daily
        </label>
        <button
          type="button"
          className={`px-3 py-1 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors`}
          onClick={checkNow}
          disabled={checking}
        >
          {checking ? 'Checking...' : 'Check for updates'}
        </button>
        <a
          href="https://github.com/ttlequals0/MinusPod/releases"
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-primary hover:underline"
        >
          Changelog
        </a>
      </div>
    </div>
  );
}
