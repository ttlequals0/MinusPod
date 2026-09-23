import { useState } from 'react';
import CollapsibleSection from '../../components/CollapsibleSection';
import { downloadConfig, downloadDiagnostics } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { useTransientState } from '../../hooks/useTransientState';
import { btnSecondary } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';

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
        <div className="p-4 rounded-lg border border-border bg-background flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <svg className="h-5 w-5 text-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 17v2a2 2 0 002 2h14a2 2 0 002-2v-2" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 3h10l4 4v6H3V7l4-4z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-foreground">Configuration Export</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download redacted settings and feed configuration as JSON for a bug report.
              </p>
            </div>
          </div>
          <button
            onClick={handleDownloadConfig}
            disabled={configStatus === 'loading'}
            className={`mt-auto min-h-[44px] w-full px-4 py-2 rounded-lg ${btnSecondary} disabled:opacity-50 transition-colors text-sm font-medium ${focusRing}`}
          >
            {configStatus === 'loading' ? 'Preparing download' : 'Download Configuration'}
          </button>
          {renderStatusIndicator(configStatus, configError)}
        </div>

        <div className="p-4 rounded-lg border border-border bg-background flex flex-col">
          <div className="flex items-start gap-3 mb-3">
            <div className="p-2 rounded bg-secondary shrink-0">
              <svg className="h-5 w-5 text-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 17v2a2 2 0 002 2h14a2 2 0 002-2v-2" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 3h10l4 4v6H3V7l4-4z" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h4 className="text-sm font-semibold text-foreground">Diagnostic Export</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Download event times and source locations without message text.
              </p>
            </div>
          </div>
          <label htmlFor="diagnosticRange" className="mb-2 text-xs font-medium text-muted-foreground">
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
            className={`mt-auto min-h-[44px] w-full px-4 py-2 rounded-lg ${btnSecondary} disabled:opacity-50 transition-colors text-sm font-medium ${focusRing}`}
          >
            {diagnosticStatus === 'loading' ? 'Preparing...' : 'Download Diagnostics'}
          </button>
          {renderStatusIndicator(diagnosticStatus, diagnosticError)}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default TroubleshootingSection;
