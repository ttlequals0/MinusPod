import type { SystemStatus } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatUptime, formatDuration, formatTokenCount, formatCost, formatStorage } from './settingsUtils';
import UpdateStatusPanel from './UpdateStatusPanel';
import { focusRing } from '../../components/fieldStyles';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  checkpointDatabase, getProcessingAdmission, setProcessingAdmission,
} from '../../api/settings';
import { btnSecondary } from '../../components/buttonStyles';

interface SystemStatusSectionProps {
  status: SystemStatus | undefined;
  statusLoading: boolean;
}

function SystemStatusSection({
  status,
  statusLoading,
}: SystemStatusSectionProps) {
  const checkpoint = useMutation({ mutationFn: checkpointDatabase });
  const queryClient = useQueryClient();
  const admission = useQuery({
    queryKey: ['processing-admission'], queryFn: getProcessingAdmission,
  });
  const updateAdmission = useMutation({
    mutationFn: (paused: boolean) => setProcessingAdmission(paused),
    onSuccess: (data) => queryClient.setQueryData(['processing-admission'], data),
  });
  const database = status?.database;
  return (
    <CollapsibleSection title="System Status" defaultOpen storageKey="settings-section-system-status">
      {statusLoading ? (
        <LoadingSpinner size="sm" />
      ) : status ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div>
            <p className="text-sm text-muted-foreground">Version</p>
            <a
              href="https://github.com/ttlequals0/minuspod"
              target="_blank"
              rel="noopener noreferrer"
              className={`font-medium text-primary hover:underline ${focusRing}`}
            >
              {status.version}
            </a>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">Feeds</p>
            <p className="font-medium text-foreground">{status.feeds?.total ?? 0}</p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">Episodes</p>
            <p className="font-medium text-foreground">{status.episodes?.total ?? 0}</p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">Storage</p>
            <p className="font-medium text-foreground">{formatStorage(status.storage?.usedMb ?? 0)}</p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">Uptime</p>
            <p className="font-medium text-foreground">{formatUptime(status.uptime ?? 0)}</p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">Time Saved</p>
            <p className="font-medium text-foreground">{formatDuration(status.stats?.totalTimeSaved ?? 0)}</p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">LLM Tokens</p>
            <p className="font-medium text-foreground">
              {formatTokenCount(status.stats?.totalInputTokens ?? 0)} in / {formatTokenCount(status.stats?.totalOutputTokens ?? 0)} out
            </p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">LLM Cost</p>
            <p className="font-medium text-foreground">{formatCost(status.stats?.totalLlmCost ?? 0)}</p>
          </div>
        </div>
      ) : null}
      {admission.data ? (
        <div className="mt-5 pt-5 border-t border-border flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium text-foreground">Processing admission</h3>
            <p className="text-xs text-muted-foreground">
              {admission.data.paused
                ? `${admission.data.activeRuns} active runs are draining. New work stays queued.`
                : `${admission.data.activeRuns} active runs and ${admission.data.queuedEpisodes} queued episodes.`}
            </p>
          </div>
          <button
            type="button"
            onClick={() => updateAdmission.mutate(!admission.data.paused)}
            disabled={updateAdmission.isPending}
            className={`px-3 py-2 rounded-lg text-sm ${btnSecondary} ${focusRing} disabled:opacity-50`}
          >
            {admission.data.paused ? 'Resume new work' : 'Pause new work'}
          </button>
        </div>
      ) : null}
      {database ? (
        <div className="mt-5 pt-5 border-t border-border space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium text-foreground">SQLite diagnostics</h3>
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
      ) : null}
      <UpdateStatusPanel />
    </CollapsibleSection>
  );
}

export default SystemStatusSection;
