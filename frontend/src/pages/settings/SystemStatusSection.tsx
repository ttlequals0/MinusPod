import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getChangelog } from '../../api/settings';
import type { SystemStatus } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatUptime, formatStorage } from './settingsUtils';
import UpdateStatusPanel from './UpdateStatusPanel';
import SystemHealthPanel from '../../components/SystemHealthPanel';
import { focusRing } from '../../components/fieldStyles';
import ToggleSwitch from '../../components/ToggleSwitch';
import WhatsNewDialog from '../../components/WhatsNewDialog';

interface SystemStatusSectionProps {
  status: SystemStatus | undefined;
  statusLoading: boolean;
  whatsNewOnUpgrade: boolean;
  onWhatsNewOnUpgradeChange: (enabled: boolean) => void;
}

function SystemStatusSection({
  status,
  statusLoading,
  whatsNewOnUpgrade,
  onWhatsNewOnUpgradeChange,
}: SystemStatusSectionProps) {
  const [showWhatsNew, setShowWhatsNew] = useState(false);
  const changelog = useQuery({
    queryKey: ['changelog', 'recent'],
    queryFn: () => getChangelog({ limit: 10 }),
    enabled: showWhatsNew,
  });
  return (
    <CollapsibleSection title="System status" defaultOpen storageKey="settings-section-system-status">
      {statusLoading ? (
        <LoadingSpinner size="sm" />
      ) : status ? (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <div>
            <p className="text-sm text-muted-foreground">Version</p>
            <button
              type="button"
              onClick={() => setShowWhatsNew(true)}
              className={`font-medium text-primary hover:underline ${focusRing}`}
            >
              {status.version}
            </button>
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
        <label className="mt-4 flex items-center gap-3 cursor-pointer">
          <ToggleSwitch
            checked={whatsNewOnUpgrade}
            onChange={onWhatsNewOnUpgradeChange}
            ariaLabel="Show what's new after an upgrade"
          />
          <span className="text-sm font-medium text-foreground">Show what's new after an upgrade</span>
        </label>
      )}
      {status && showWhatsNew && (
        <WhatsNewDialog
          current={status.version}
          sections={changelog.data?.sections}
          isError={changelog.isError}
          onClose={() => setShowWhatsNew(false)}
        />
      )}
      {status && <SystemHealthPanel status={status} />}
      <UpdateStatusPanel />
    </CollapsibleSection>
  );
}

export default SystemStatusSection;
