import { useState } from 'react';
import { FileDown } from 'lucide-react';
import CollapsibleSection from '../../components/CollapsibleSection';
import { downloadConfig, downloadDiagnostics } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { useTransientState } from '../../hooks/useTransientState';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { selectBase, filterLabel } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';

type ActionStatus = 'idle' | 'loading' | 'success' | 'error';

function TroubleshootingSection() {
  const [configStatus, setConfigStatus] = useTransientState<ActionStatus>('idle', 3000);
  const [configError, setConfigError] = useState('');
  const [diagnosticStatus, setDiagnosticStatus] = useTransientState<ActionStatus>('idle', 3000);
  const [diagnosticError, setDiagnosticError] = useState('');
  const [diagnosticRangeHours, setDiagnosticRangeHours] = useState('24');

  const handleDownloadConfig = async () => {
    setConfigStatus('loading', null);
    setConfigError('');
    try {
      await downloadConfig();
      setConfigStatus('success');
    } catch (err) {
      setConfigStatus('error', 5000);
      setConfigError(getErrorMessage(
        err, 'Could not build the configuration file. Try again, or check the server log.'));
    }
  };

  const handleDownloadDiagnostics = async () => {
    setDiagnosticStatus('loading', null);
    setDiagnosticError('');
    try {
      const end = new Date();
      const start = new Date(end.getTime() - Number(diagnosticRangeHours) * 60 * 60 * 1000);
      await downloadDiagnostics(start.toISOString(), end.toISOString());
      setDiagnosticStatus('success');
    } catch (err) {
      setDiagnosticStatus('error', 5000);
      setDiagnosticError(getErrorMessage(err, 'Could not download diagnostics'));
    }
  };

  const renderStatusIndicator = (status: ActionStatus, error: string) => {
    if (status === 'loading') {
      return <p className="mt-3 text-xs text-muted-foreground">Processing...</p>;
    }
    if (status === 'success') {
      return <p className="mt-3 text-xs text-success">Downloaded successfully</p>;
    }
    if (status === 'error' && error) {
      return <p className="mt-3 text-xs text-destructive">{error}</p>;
    }
    return null;
  };

  return (
    <CollapsibleSection
      title="Troubleshooting"
      subtitle="Export safe diagnostics or a redacted configuration when reporting a problem."
      storageKey="settings-section-troubleshooting"
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 items-stretch gap-4">
        <div className="p-4 rounded-lg bg-muted/40 flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <FileDown className="w-5 h-5 text-foreground" aria-hidden="true" />
            </div>
            <div className="flex-1 min-w-0">
              <h4 className={subHeading}>Configuration export</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download redacted settings and feed configuration as JSON for a bug report.
              </p>
            </div>
          </div>
          <button
            onClick={handleDownloadConfig}
            disabled={configStatus === 'loading'}
            className={`${btnBase} ${btnSecondary} mt-auto w-full`}
          >
            {configStatus === 'loading' ? 'Preparing download' : 'Download configuration'}
          </button>
          {renderStatusIndicator(configStatus, configError)}
        </div>

        <div className="p-4 rounded-lg bg-muted/40 flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <FileDown className="w-5 h-5 text-foreground" aria-hidden="true" />
            </div>
            <div className="flex-1 min-w-0">
              <h4 className={subHeading}>Diagnostic export</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download event times and source locations without message text.
              </p>
            </div>
          </div>
          <label htmlFor="diagnosticRange" className={filterLabel}>
            Time range
          </label>
          <select
            id="diagnosticRange"
            aria-label="Diagnostic time range"
            value={diagnosticRangeHours}
            onChange={(event) => setDiagnosticRangeHours(event.target.value)}
            className={`mb-3 w-full ${selectBase}`}
          >
            <option value="1">Last hour</option>
            <option value="6">Last 6 hours</option>
            <option value="24">Last 24 hours</option>
          </select>
          <button
            onClick={handleDownloadDiagnostics}
            disabled={diagnosticStatus === 'loading'}
            className={`${btnBase} ${btnSecondary} mt-auto w-full`}
          >
            {diagnosticStatus === 'loading' ? 'Preparing...' : 'Download diagnostics'}
          </button>
          {renderStatusIndicator(diagnosticStatus, diagnosticError)}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default TroubleshootingSection;
