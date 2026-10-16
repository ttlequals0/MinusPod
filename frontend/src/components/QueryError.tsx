import { btnSecondary, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';

export default function QueryError({ title, onRetry, retrying = false }: {
  title: string;
  onRetry: () => void;
  retrying?: boolean;
}) {
  return (
    <div role="alert" className="rounded-lg border border-destructive/40 bg-card p-4 space-y-3">
      <p className="text-destructive">{title}</p>
      <button type="button" onClick={onRetry} disabled={retrying}
        className={`${btnSecondary} ${touchTarget} ${focusRing} rounded px-3 py-2 text-sm disabled:opacity-50`}>
        {retrying ? 'Retrying...' : 'Retry'}
      </button>
    </div>
  );
}
