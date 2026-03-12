import type { SystemStatus } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatUptime, formatDuration, formatTokenCount, formatCost, formatStorage } from './settingsUtils';

interface SystemStatusSectionProps {
  status: SystemStatus | undefined;
  statusLoading: boolean;
  cleanupConfirm: boolean;
  cleanupIsPending: boolean;
  cleanupData: { episodesRemoved: number; spaceFreedMb?: number } | undefined;
  onCleanup: () => void;
}

function SystemStatusSection({
  status,
  statusLoading,
  cleanupConfirm,
  cleanupIsPending,
  cleanupData,
  onCleanup,
}: SystemStatusSectionProps) {
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
              className="font-medium text-primary hover:underline"
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
      <div className="mt-4 pt-4 border-t border-border">
        <button
          onClick={onCleanup}
          disabled={cleanupIsPending}
          className={`px-4 py-2 rounded transition-colors disabled:opacity-50 ${
            cleanupConfirm
              ? 'bg-destructive text-destructive-foreground hover:bg-destructive/80'
              : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
          }`}
        >
          {cleanupIsPending
            ? 'Resetting...'
            : cleanupConfirm
            ? 'Click again to confirm'
            : 'Reset All Episodes'}
        </button>
        {cleanupData && (
          <span className="ml-3 text-sm text-muted-foreground">
            Reset {cleanupData.episodesRemoved} episodes, freed {formatStorage(cleanupData.spaceFreedMb ?? 0)}
          </span>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default SystemStatusSection;
