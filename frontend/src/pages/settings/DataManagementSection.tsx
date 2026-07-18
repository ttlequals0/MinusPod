import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import DropdownMenu, { DropdownMenuItem } from '../../components/DropdownMenu';
import CollapsibleSection from '../../components/CollapsibleSection';
import ConfirmResetButton from './ConfirmResetButton';
import NumberInput from '../../components/NumberInput';
import { exportOpml, getSettings, downloadBackup } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { useTransientState } from '../../hooks/useTransientState';
import { copyText } from '../../utils/clipboard';
import { BYTES_PER_MB, formatStorage } from './settingsUtils';
import { btnSecondary } from '../../components/buttonStyles';


type ActionStatus = 'idle' | 'loading' | 'success' | 'error';

interface DataManagementSectionProps {
  onResetEpisodes: () => void;
  resetIsPending: boolean;
  resetData: { episodesRemoved: number; spaceFreedMb: number } | undefined;
  maxRssBytes: number;
  onMaxRssBytesChange: (bytes: number) => void;
}

function DataManagementSection({
  onResetEpisodes,
  resetIsPending,
  resetData,
  maxRssBytes,
  onMaxRssBytesChange,
}: DataManagementSectionProps) {
  // 'loading' is set with ms=null (persists until the request settles);
  // 'success' auto-resets after the 3s default, 'error' after 5s.
  const [opmlStatus, setOpmlStatus] = useTransientState<ActionStatus>('idle', 3000);
  const [opmlError, setOpmlError] = useState('');
  const [backupStatus, setBackupStatus] = useTransientState<ActionStatus>('idle', 3000);
  const [backupError, setBackupError] = useState('');

  // opmlModifiedUrl/opmlOriginalUrl are non-null only when feed auth is on;
  // Copy URL is hidden otherwise (the /opml route 404s without a key).
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const [opmlCopied, setOpmlCopied] = useTransientState<'modified' | 'original' | null>(null, 2000);

  const handleCopyOpmlUrl = async (mode: 'modified' | 'original', url: string) => {
    if (await copyText(url)) setOpmlCopied(mode);
  };

  const handleExportOpml = async (mode: 'original' | 'modified' = 'original') => {
    setOpmlStatus('loading', null);
    setOpmlError('');
    try {
      await exportOpml(mode);
      setOpmlStatus('success');
    } catch (err) {
      setOpmlStatus('error', 5000);
      setOpmlError(getErrorMessage(err, 'Export failed'));
    }
  };

  const handleDownloadBackup = async () => {
    setBackupStatus('loading', null);
    setBackupError('');
    try {
      await downloadBackup();
      setBackupStatus('success');
    } catch (err) {
      setBackupStatus('error', 5000);
      setBackupError(getErrorMessage(err, 'Backup failed'));
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
            <div className="p-2 rounded bg-secondary shrink-0">
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
            {(['modified', 'original'] as const).map((mode) => {
              const url = mode === 'modified' ? settings?.opmlModifiedUrl : settings?.opmlOriginalUrl;
              const items: DropdownMenuItem[] = [];
              if (url) {
                items.push({
                  title: opmlCopied === mode ? 'Copied' : 'Copy URL',
                  subtitle: 'For apps that import from URL',
                  onClick: () => handleCopyOpmlUrl(mode, url),
                });
              }
              items.push({
                title: 'Download file',
                subtitle: 'Save the .opml file',
                onClick: () => handleExportOpml(mode),
              });
              return (
                <div key={mode} className="flex-1">
                  <DropdownMenu
                    triggerLabel={mode === 'modified' ? 'Modified Feeds' : 'Original Feeds'}
                    triggerClassName={`w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg ${btnSecondary} disabled:opacity-50 transition-colors text-sm font-medium`}
                    disabled={opmlStatus === 'loading'}
                    title={mode === 'modified' ? 'Export modified feeds' : 'Export original feeds'}
                    align={mode === 'modified' ? 'left' : 'right'}
                    items={items}
                  />
                </div>
              );
            })}
          </div>
          {renderStatusIndicator(opmlStatus, opmlError)}
        </div>

        {/* Database Backup Card */}
        <div className="p-4 rounded-lg border border-border bg-background flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <svg className="h-5 w-5 text-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <ellipse cx="12" cy="5" rx="9" ry="3" />
                <path d="M21 12c0 1.66-4.03 3-9 3s-9-1.34-9-3" />
                <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-foreground">Database Backup</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download a backup of feeds, episodes, patterns, sponsors, and settings.
              </p>
            </div>
          </div>
          <button
            onClick={handleDownloadBackup}
            disabled={backupStatus === 'loading'}
            className={`mt-auto w-full px-4 py-2 rounded-lg ${btnSecondary} disabled:opacity-50 transition-colors text-sm font-medium`}
          >
            {backupStatus === 'loading' ? 'Preparing...' : 'Download Backup'}
          </button>
          {renderStatusIndicator(backupStatus, backupError)}
        </div>
      </div>

      <div className="mt-4 pt-4 border-t border-border">
        <label htmlFor="maxRssMb" className="block text-sm font-medium text-foreground mb-2">
          Max RSS feed size (MB)
        </label>
        <div className="flex items-center gap-3">
          <NumberInput
            id="maxRssMb"
            value={Math.round(maxRssBytes / BYTES_PER_MB)}
            min={1}
            max={1048576}
            fallback={200}
            parse={(s) => parseInt(s, 10)}
            onCommit={(mb) => {
              // No backend ceiling (deliberately) and commit only real
              // edits: a focus/blur must not clamp or re-round an
              // env-configured value the user never touched.
              if (mb !== Math.round(maxRssBytes / BYTES_PER_MB)) onMaxRssBytesChange(mb * BYTES_PER_MB);
            }}
          />
          <span className="text-sm text-muted-foreground">MB (minimum 1)</span>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          Upstream RSS feeds over this size are rejected during refresh. Default 200 MB.
        </p>
      </div>

      <div className="mt-4 pt-4 border-t border-border">
        <ConfirmResetButton
          label="Reset All Episodes"
          isPending={resetIsPending}
          onConfirm={onResetEpisodes}
        />
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
