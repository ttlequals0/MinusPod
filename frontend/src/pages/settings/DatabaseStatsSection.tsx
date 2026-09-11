import { useMutation } from '@tanstack/react-query';
import type { SystemStatus } from '../../api/types';
import { checkpointDatabase } from '../../api/settings';
import { btnSecondary } from '../../components/buttonStyles';
import CollapsibleSection from '../../components/CollapsibleSection';
import { focusRing } from '../../components/fieldStyles';
import { formatStorage } from './settingsUtils';

interface DatabaseStatsSectionProps {
  database: SystemStatus['database'];
}

function DatabaseStatsSection({ database }: DatabaseStatsSectionProps) {
  const checkpoint = useMutation({ mutationFn: checkpointDatabase });

  if (!database) return null;

  return (
    <CollapsibleSection title="Database Stats" storageKey="settings-section-db-stats">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs text-muted-foreground">
              Counters cover worker process {database.instrumentation.processId} and reset when it restarts.
            </p>
          </div>
          <button
            type="button"
            onClick={() => checkpoint.mutate()}
            disabled={checkpoint.isPending}
            className={`px-3 py-2 rounded-lg text-sm ${btnSecondary} ${focusRing} disabled:opacity-50`}
          >
            {checkpoint.isPending ? 'Checkpointing...' : 'Run passive checkpoint'}
          </button>
        </div>
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
          <div><dt className="text-muted-foreground">Journal</dt><dd className="font-medium text-foreground uppercase">{database.journalMode}</dd></div>
          <div><dt className="text-muted-foreground">Busy timeout</dt><dd className="font-medium text-foreground">{database.busyTimeoutMs} ms</dd></div>
          <div><dt className="text-muted-foreground">Database</dt><dd className="font-medium text-foreground">{formatStorage(database.databaseBytes / 1024 / 1024)}</dd></div>
          <div><dt className="text-muted-foreground">WAL</dt><dd className="font-medium text-foreground">{formatStorage(database.walBytes / 1024 / 1024)}</dd></div>
          <div><dt className="text-muted-foreground">Free pages</dt><dd className="font-medium text-foreground">{database.freelistPages}</dd></div>
          <div><dt className="text-muted-foreground">Slow statements</dt><dd className="font-medium text-foreground">{database.instrumentation.slowStatements}</dd></div>
          <div><dt className="text-muted-foreground">Long transactions</dt><dd className="font-medium text-foreground">{database.instrumentation.longTransactions}</dd></div>
          <div><dt className="text-muted-foreground">Longest commit</dt><dd className="font-medium text-foreground">{database.instrumentation.maxCommitMs.toFixed(2)} ms</dd></div>
        </dl>
        {checkpoint.data ? (
          <p className="text-xs text-success" role="status">
            Checkpointed {checkpoint.data.checkpointedPages} of {checkpoint.data.logPages} WAL pages in {checkpoint.data.durationMs.toFixed(2)} ms.
          </p>
        ) : null}
        {checkpoint.isError ? (
          <p className="text-xs text-destructive" role="alert">Checkpoint could not complete.</p>
        ) : null}
      </div>
    </CollapsibleSection>
  );
}

export default DatabaseStatsSection;
