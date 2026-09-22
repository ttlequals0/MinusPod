import type { SystemStatus } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatUptime, formatDuration, formatTokenCount, formatCost, formatStorage } from './settingsUtils';
import UpdateStatusPanel from './UpdateStatusPanel';
import SystemHealthPanel from '../../components/SystemHealthPanel';
import { focusRing } from '../../components/fieldStyles';

interface SystemStatusSectionProps {
  status: SystemStatus | undefined;
  statusLoading: boolean;
}

function SystemStatusSection({
  status,
  statusLoading,
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
              <span className="block">{formatTokenCount(status.stats?.totalInputTokens ?? 0)} in</span>
              <span className="block">{formatTokenCount(status.stats?.totalOutputTokens ?? 0)} out</span>
            </p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">LLM Cost</p>
            <p className="font-medium text-foreground">{formatCost(status.stats?.totalLlmCost ?? 0)}</p>
          </div>
        </div>
      ) : null}
      {status && <SystemHealthPanel status={status} />}
      <UpdateStatusPanel />
    </CollapsibleSection>
  );
}

export default SystemStatusSection;
