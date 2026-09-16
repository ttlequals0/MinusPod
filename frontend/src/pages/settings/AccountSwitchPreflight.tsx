import { useQuery } from '@tanstack/react-query';
import { getAffectedRuns } from '../../api/providers';
import type { AffectedRunsAction, ProviderSlot } from '../../api/types';
import SegmentedToggle from '../../components/SegmentedToggle';

const ACTION_OPTIONS = [
  { value: 'requeue' as const, label: 'Requeue on the new account' },
  { value: 'cancel' as const, label: 'Cancel those runs' },
];

interface AccountSwitchPreflightProps {
  slot: ProviderSlot;
  /** The form's endpoint or provider type differs from what is saved. */
  changed: boolean;
  action: AffectedRunsAction;
  onActionChange: (action: AffectedRunsAction) => void;
}

// A slot's endpoint or provider type changing moves in-flight work to another
// account, so the runs bound to the old one are shown before Save and the
// operator picks what happens to them.
function AccountSwitchPreflight({ slot, changed, action, onActionChange }: AccountSwitchPreflightProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['affected-runs', slot],
    queryFn: () => getAffectedRuns(slot),
    enabled: changed,
    retry: false,
  });

  if (!changed) return null;
  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Checking for work on the current account...</p>;
  }
  if (isError) {
    return (
      <p className="text-sm text-muted-foreground">
        This build cannot list the runs bound to the current account. Save will not reassign them.
      </p>
    );
  }
  if (!data || data.count === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No runs are bound to the current {slot} account, so this switch affects nothing in flight.
      </p>
    );
  }

  return (
    <div className="rounded-lg border border-warning/50 bg-warning/10 p-4">
      <p className="text-sm font-medium text-foreground">
        {data.count} {data.count === 1 ? 'run is' : 'runs are'} still bound to the current {slot} account
      </p>
      <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
        {data.runs.map((run) => (
          <li key={run.id} className="truncate">
            {run.title} <span className="text-foreground">({run.state})</span>
          </li>
        ))}
      </ul>
      {data.runs.length < data.count && (
        <p className="mt-1 text-xs text-muted-foreground">
          {data.count - data.runs.length} more not listed.
        </p>
      )}
      <div className="mt-3">
        <SegmentedToggle
          options={ACTION_OPTIONS}
          value={action}
          onChange={onActionChange}
          ariaLabel={`What to do with runs on the old ${slot} account`}
        />
      </div>
    </div>
  );
}

export default AccountSwitchPreflight;
