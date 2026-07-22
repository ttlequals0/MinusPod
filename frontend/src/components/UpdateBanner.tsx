import { useQuery } from '@tanstack/react-query';
import { getUpdateCheckSettings, getUpdateStatus } from '../api/updates';
import { useLocalStorageState } from '../hooks/useLocalStorageState';

export default function UpdateBanner() {
  const [dismissed, setDismissed] = useLocalStorageState<string>('update-banner-dismissed', '');
  const { data: settings } = useQuery({
    queryKey: ['update-check-settings'],
    queryFn: getUpdateCheckSettings,
    staleTime: 6 * 60 * 60 * 1000,
  });
  const { data: status } = useQuery({
    queryKey: ['update-status'],
    queryFn: () => getUpdateStatus(),
    staleTime: 6 * 60 * 60 * 1000,
    refetchOnWindowFocus: false,
    enabled: settings?.enabled === true,
  });

  if (!status?.updateAvailable) return null;
  const target = status.channel === 'stable' ? status.stable : status.edge;
  if (!target || dismissed === target.version) return null;

  return (
    <div className="mb-4 flex items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
      <span>
        MinusPod {target.version} is available
        {target.url ? (
          <>
            {' ('}
            <a href={target.url} target="_blank" rel="noopener noreferrer" className="underline">
              release notes
            </a>
            {')'}
          </>
        ) : null}
        .
      </span>
      <button
        type="button"
        aria-label="Dismiss"
        className="shrink-0 rounded px-2 py-1 hover:bg-amber-500/20"
        onClick={() => setDismissed(target.version)}
      >
        Dismiss
      </button>
    </div>
  );
}
