import { useState } from 'react';
import CollapsibleSection from '../../components/CollapsibleSection';
import { exportOpml, downloadBackup } from '../../api/settings';

type ActionStatus = 'idle' | 'loading' | 'success' | 'error';

function DataManagementSection() {
  const [opmlStatus, setOpmlStatus] = useState<ActionStatus>('idle');
  const [opmlError, setOpmlError] = useState('');
  const [backupStatus, setBackupStatus] = useState<ActionStatus>('idle');
  const [backupError, setBackupError] = useState('');

  const handleExportOpml = async () => {
    setOpmlStatus('loading');
    setOpmlError('');
    try {
      await exportOpml();
      setOpmlStatus('success');
      setTimeout(() => setOpmlStatus('idle'), 3000);
    } catch (err) {
      setOpmlStatus('error');
      setOpmlError(err instanceof Error ? err.message : 'Export failed');
      setTimeout(() => setOpmlStatus('idle'), 5000);
    }
  };

  const handleDownloadBackup = async () => {
    setBackupStatus('loading');
    setBackupError('');
    try {
      await downloadBackup();
      setBackupStatus('success');
      setTimeout(() => setBackupStatus('idle'), 3000);
    } catch (err) {
      setBackupStatus('error');
      setBackupError(err instanceof Error ? err.message : 'Backup failed');
      setTimeout(() => setBackupStatus('idle'), 5000);
    }
  };

  return (
    <CollapsibleSection title="Data Management" storageKey="settings-section-data-management">
      <div className="space-y-4">
        <div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleExportOpml}
              disabled={opmlStatus === 'loading'}
              className="px-4 py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm"
            >
              {opmlStatus === 'loading' ? 'Exporting...' : 'Export OPML'}
            </button>
            {opmlStatus === 'success' && (
              <span className="text-sm text-green-500">Downloaded</span>
            )}
            {opmlError && (
              <span className="text-sm text-destructive">{opmlError}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Export all feed subscriptions as an OPML file for backup or import into other apps.
          </p>
        </div>

        <div className="border-t border-border pt-4">
          <div className="flex items-center gap-3">
            <button
              onClick={handleDownloadBackup}
              disabled={backupStatus === 'loading'}
              className="px-4 py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm"
            >
              {backupStatus === 'loading' ? 'Preparing...' : 'Download Database Backup'}
            </button>
            {backupStatus === 'success' && (
              <span className="text-sm text-green-500">Downloaded</span>
            )}
            {backupError && (
              <span className="text-sm text-destructive">{backupError}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Download a complete backup of your database including feeds, episodes, patterns, sponsors, and settings.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default DataManagementSection;
