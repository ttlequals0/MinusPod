import { useState } from 'react';
import { Check, Database, FileDown, X } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import DropdownMenu, { DropdownMenuItem } from '../../components/DropdownMenu';
import LoadingSpinner from '../../components/LoadingSpinner';
import CollapsibleSection from '../../components/CollapsibleSection';
import ConfirmResetButton from './ConfirmResetButton';
import NumberInput from '../../components/NumberInput';
import { exportOpml, getSettings, downloadBackup } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { useTransientState } from '../../hooks/useTransientState';
import { copyText } from '../../utils/clipboard';
import { BYTES_PER_MB, formatStorage } from './settingsUtils';
import ConfigurationTransferSection from './ConfigurationTransferSection';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { labelBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';


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
      setOpmlError(getErrorMessage(err, 'Could not export OPML.'));
    }
  };

  const handleDownloadBackup = async (encrypted = true) => {
    setBackupStatus('loading', null);
    setBackupError('');
    try {
      await downloadBackup(encrypted);
      setBackupStatus('success');
    } catch (err) {
      setBackupStatus('error', 5000);
      const message = getErrorMessage(err, 'Could not download backup.');
      setBackupError(message === 'backup_encryption_unavailable'
        ? 'Encrypted backup unavailable. Set MINUSPOD_MASTER_PASSPHRASE and restart, or download plaintext below.'
        : message);
    }
  };

  const renderStatusIndicator = (status: ActionStatus, error: string) => {
    if (status === 'loading') {
      return (
        <div className="flex items-center gap-2 mt-3">
          <LoadingSpinner inline className="w-4 h-4" />
          <span className="text-xs text-muted-foreground">Processing...</span>
        </div>
      );
    }
    if (status === 'success') {
      return (
        <div className="flex items-center gap-2 mt-3">
          <Check className="w-4 h-4 text-success" aria-hidden="true" />
          <span className="text-xs text-success">Downloaded successfully</span>
        </div>
      );
    }
    if (status === 'error' && error) {
      return (
        <div className="flex items-center gap-2 mt-3">
          <X className="w-4 h-4 text-destructive" aria-hidden="true" />
          <span className="text-xs text-destructive">{error}</span>
        </div>
      );
    }
    return null;
  };

  return (
    <CollapsibleSection title="Data management" storageKey="settings-section-data-management">
      <div className="grid grid-cols-1 sm:grid-cols-2 items-start gap-4">
        {/* OPML Export Card */}
        <div className="p-4 rounded-lg bg-muted/40 flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <FileDown className="w-5 h-5 text-foreground" aria-hidden="true" />
            </div>
            <div className="flex-1 min-w-0">
              <h4 className={subHeading}>OPML export</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Export feed subscriptions as OPML. Modified feeds use MinusPod ad-free URLs; original feeds use upstream source URLs.
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 items-stretch gap-2">
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
                <div key={mode} className="flex min-w-0 [&>div]:flex [&>div]:w-full">
                  <DropdownMenu
                    triggerLabel={mode === 'modified' ? 'Modified feeds' : 'Original feeds'}
                    triggerClassName={`${btnBase} ${btnSecondary} h-full w-full`}
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
        <div className="p-4 rounded-lg bg-muted/40 flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <Database className="w-5 h-5 text-foreground" aria-hidden="true" />
            </div>
            <div className="flex-1 min-w-0">
              <h4 className={subHeading}>Database backup</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download feeds, episodes, patterns, sponsors, and settings. Encrypted download is the default.
              </p>
            </div>
          </div>
          <button
            onClick={() => handleDownloadBackup(true)}
            disabled={backupStatus === 'loading'}
            className={`${btnBase} ${btnSecondary} mt-auto w-full`}
          >
            {backupStatus === 'loading' ? 'Preparing...' : 'Download encrypted backup'}
          </button>
          <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3">
            <p className="text-xs text-warning">
              This downloads the full database without file encryption. Protect the downloaded file.
            </p>
            <div className="mt-2">
              <ConfirmResetButton
                label="Download plaintext"
                ariaLabel="Download plaintext database backup"
                disabled={backupStatus === 'loading'}
                onConfirm={() => handleDownloadBackup(false)}
                size="compact"
                className="min-h-[44px]"
              />
            </div>
          </div>
          {renderStatusIndicator(backupStatus, backupError)}
        </div>

      </div>

      <ConfigurationTransferSection />

      <div className="mt-4 pt-4 border-t border-border">
        <label htmlFor="maxRssMb" className={labelBase}>
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
          label="Reset all episodes"
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
