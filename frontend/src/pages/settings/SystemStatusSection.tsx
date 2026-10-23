import { Link } from 'react-router';
import type { SystemStatus } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatUptime, formatStorage } from './settingsUtils';
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
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
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
        </div>
      ) : null}
      {status && (
        <p className="mt-3 text-sm text-muted-foreground">
          Time saved, tokens and cost are on the{' '}
          <Link to="/stats" className={`text-primary hover:underline ${focusRing}`}>Stats page</Link>.
        </p>
      )}
      {status && <SystemHealthPanel status={status} />}
      <UpdateStatusPanel />
    </CollapsibleSection>
  );
}

export default SystemStatusSection;
