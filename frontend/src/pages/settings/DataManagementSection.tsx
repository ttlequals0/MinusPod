import { useState } from 'react';
import CollapsibleSection from '../../components/CollapsibleSection';
import { exportOpml, downloadBackup } from '../../api/settings';
import { formatStorage } from './settingsUtils';

type ActionStatus = 'idle' | 'loading' | 'success' | 'error';

interface DataManagementSectionProps {
  onResetEpisodes: () => void;
  resetIsPending: boolean;
  resetData: { episodesRemoved: number; spaceFreedMb: number } | undefined;
}

function DataManagementSection({
  onResetEpisodes,
  resetIsPending,
  resetData,
}: DataManagementSectionProps) {
  const [opmlStatus, setOpmlStatus] = useState<ActionStatus>('idle');
  const [opmlError, setOpmlError] = useState('');
  const [backupStatus, setBackupStatus] = useState<ActionStatus>('idle');
  const [backupError, setBackupError] = useState('');
  const [resetConfirm, setResetConfirm] = useState(false);

  const handleExportOpml = async (mode: 'original' | 'modified' = 'original') => {
    setOpmlStatus('loading');
    setOpmlError('');
    try {
      await exportOpml(mode);
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

  const renderStatusIndicator = (status: ActionStatus, error: string) => {
    if (status === 'loading') {
      return (
        <div className="flex items-center gap-2 mt-3">
          <svg className="animate-spin h-4 w-4 text-muted-foreground" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span className="text-xs text-muted-foreground">Processing...</span>
        </div>
      );
    }
    if (status === 'success') {
      return (
        <div className="flex items-center gap-2 mt-3">
          <svg className="h-4 w-4 text-green-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          <span className="text-xs text-green-500">Downloaded successfully</span>
        </div>
      );
    }
    if (status === 'error' && error) {
      return (
        <div className="flex items-center gap-2 mt-3">
          <svg className="h-4 w-4 text-destructive" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
          <span className="text-xs text-destructive">{error}</span>
        </div>
      );
    }
    return null;
  };

  return (
    <CollapsibleSection title="Data Management" storageKey="settings-section-data-management">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* OPML Export Card */}
        <div className="p-4 rounded-lg border border-border bg-background flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary flex-shrink-0">
              <svg className="h-5 w-5 text-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 17v2a2 2 0 002 2h14a2 2 0 002-2v-2" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 3h10l4 4v6H3V7l4-4z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-foreground">OPML Export</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Export feed subscriptions as OPML. Modified feeds use MinusPod ad-free URLs; original feeds use upstream source URLs.
              </p>
            </div>
          </div>
          <div className="flex gap-2 mt-auto">
            <button
              onClick={() => handleExportOpml('modified')}
              disabled={opmlStatus === 'loading'}
              className="flex-1 px-3 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm font-medium"
            >
              {opmlStatus === 'loading' ? 'Exporting...' : 'Modified Feeds'}
            </button>
            <button
              onClick={() => handleExportOpml('original')}
              disabled={opmlStatus === 'loading'}
              className="flex-1 px-3 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm font-medium"
            >
              {opmlStatus === 'loading' ? 'Exporting...' : 'Original Feeds'}
            </button>
          </div>
          {renderStatusIndicator(opmlStatus, opmlError)}
        </div>

        {/* Database Backup Card */}
        <div className="p-4 rounded-lg border border-border bg-background flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary flex-shrink-0">
              <svg className="h-5 w-5 text-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <ellipse cx="12" cy="5" rx="9" ry="3" />
                <path d="M21 12c0 1.66-4.03 3-9 3s-9-1.34-9-3" />
                <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-foreground">Database Backup</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download a complete backup including feeds, episodes, patterns, sponsors, and settings.
              </p>
            </div>
          </div>
          <button
            onClick={handleDownloadBackup}
            disabled={backupStatus === 'loading'}
            className="mt-auto w-full px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm font-medium"
          >
            {backupStatus === 'loading' ? 'Preparing...' : 'Download Backup'}
          </button>
          {renderStatusIndicator(backupStatus, backupError)}
        </div>
      </div>

      <div className="mt-4 pt-4 border-t border-border">
        <button
          onClick={() => {
            if (resetConfirm) {
              onResetEpisodes();
              setResetConfirm(false);
            } else {
              setResetConfirm(true);
              setTimeout(() => setResetConfirm(false), 3000);
            }
          }}
          disabled={resetIsPending}
          className={`px-4 py-2 rounded transition-colors disabled:opacity-50 ${
            resetConfirm
              ? 'bg-destructive text-destructive-foreground hover:bg-destructive/80'
              : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
          }`}
        >
          {resetIsPending
            ? 'Resetting...'
            : resetConfirm
            ? 'Click again to confirm'
            : 'Reset All Episodes'}
        </button>
        {resetData && (
          <span className="ml-3 text-sm text-muted-foreground">
            Reset {resetData.episodesRemoved} episodes, freed {formatStorage(resetData.spaceFreedMb ?? 0)}
          </span>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default DataManagementSection;
