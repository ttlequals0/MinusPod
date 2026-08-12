import { useState } from 'react';
import type { ProviderName, ProviderStatus } from '../../api/providers';
import { getErrorMessage } from '../../api/client';
import { ConfirmModal } from '../../components/Modal';
import { useTransientState } from '../../hooks/useTransientState';
import { focusRing } from '../../components/fieldStyles';
import { btnOutline, btnPrimary } from '../../components/buttonStyles';

interface ProviderKeyFieldProps {
  provider: ProviderName;
  status: ProviderStatus;
  cryptoReady: boolean;
  placeholder: string;
  label?: string;
  helper?: string;
  onSave: (provider: ProviderName, apiKey: string) => Promise<void>;
  onClear: (provider: ProviderName) => Promise<void>;
  onTest: (provider: ProviderName) => Promise<{ ok: boolean; error?: string }>;
}

const CHIP = {
  db:   { bg: 'bg-success/10 text-success', dot: 'bg-success', text: 'Stored encrypted' },
  env:  { bg: 'bg-warning/10 text-warning', dot: 'bg-warning', text: 'Using env fallback' },
  none: { bg: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/60', text: 'Not set' },
} as const;

function StatusChip({ source }: { source: ProviderStatus['source'] }) {
  const c = CHIP[source];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium ${c.bg}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {c.text}
    </span>
  );
}

function ProviderKeyField({
  provider, status, cryptoReady, placeholder, label = 'API key', helper,
  onSave, onClear, onTest,
}: ProviderKeyFieldProps) {
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | 'clear' | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [savedNotice, setSavedNotice] = useTransientState(false, 4000);
  const [error, setError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const showActions = status.source === 'db' || draft.length > 0;
  // Test reads the SAVED key from the backend, not the draft. If the user
  // typed a value but hasn't clicked Save, Test would always return "no key
  // configured" because the draft only exists in React state. Disable Test
  // in that window and explain why via the title tooltip.
  const testBlocked = draft.length > 0 && status.source !== 'db';

  async function handleSave() {
    if (!draft) return;
    setBusy('save'); setError(null); setTestResult(null); setSavedNotice(false);
    try {
      await onSave(provider, draft);
      setDraft('');
      // Make the field-clearing intentional: the input goes blank because the
      // key is now encrypted in the DB and we don't echo secrets back. Without
      // this notice, users misread the blank input as "save erased my key".
      setSavedNotice(true);
    }
    catch (e) { setError(getErrorMessage(e, 'Save failed')); }
    finally { setBusy(null); }
  }

  async function doClear() {
    setConfirmClear(false);
    setBusy('clear'); setError(null); setTestResult(null);
    try { await onClear(provider); setDraft(''); }
    catch (e) { setError(getErrorMessage(e, 'Clear failed')); }
    finally { setBusy(null); }
  }

  async function handleTest() {
    setBusy('test'); setTestResult(null);
    try {
      const r = await onTest(provider);
      setTestResult({ ok: r.ok, msg: r.ok ? 'OK' : (r.error || 'failed') });
    } catch (e) {
      setTestResult({ ok: false, msg: getErrorMessage(e, 'failed') });
    } finally { setBusy(null); }
  }

  if (!cryptoReady) {
    return (
      <div>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-sm font-medium text-foreground">{label}</span>
          <StatusChip source={status.source} />
        </div>
        <p className="text-sm text-muted-foreground">
          Setup required: set <code className="font-mono">MINUSPOD_MASTER_PASSPHRASE</code> in the container environment to store keys here.
          {status.source === 'env' && ' The environment variable is active.'}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <label htmlFor={`key-${provider}`} className="text-sm font-medium text-foreground">{label}</label>
        <StatusChip source={status.source} />
      </div>
      <input
        id={`key-${provider}`}
        type="password"
        autoComplete="off"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={status.source === 'db' ? '(stored - enter new value to change)' : placeholder}
        className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm"
      />
      {helper && <p className="mt-1 text-sm text-muted-foreground">{helper}</p>}
      {showActions && (
        <div className="flex items-center gap-2 mt-2">
          <button
            type="button"
            disabled={!draft || busy !== null}
            onClick={handleSave}
            className={`px-3 py-1.5 rounded-md ${btnPrimary} text-sm font-medium transition-colors disabled:opacity-50 ${focusRing}`}
          >
            {busy === 'save' ? 'Saving...' : 'Save'}
          </button>
          <button
            type="button"
            disabled={busy !== null || testBlocked}
            onClick={handleTest}
            title={testBlocked ? 'Click Save first -- Test reads the saved key, not the unsaved draft.' : undefined}
            className={`px-3 py-1.5 rounded-md ${btnOutline} text-sm font-medium transition-colors disabled:opacity-50 ${focusRing}`}
          >
            {busy === 'test' ? 'Testing...' : 'Test'}
          </button>
          {status.source === 'db' && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => setConfirmClear(true)}
              className={`px-3 py-1.5 rounded-md ${btnOutline} text-destructive text-sm font-medium transition-colors disabled:opacity-50 ${focusRing}`}
            >
              Clear
            </button>
          )}
          {testResult && (
            <span className={`text-sm ${testResult.ok ? 'text-success' : 'text-destructive'}`}>
              {testResult.msg}
            </span>
          )}
          {savedNotice && (
            <span className="text-sm text-success">
              Saved -- input cleared because keys are stored encrypted
            </span>
          )}
        </div>
      )}
      {error && <p className="mt-1 text-sm text-destructive">{error}</p>}
      {confirmClear && (
        <ConfirmModal
          title={`Remove stored ${provider} key?`}
          confirmLabel="Remove key"
          busyLabel="Removing..."
          pending={busy === 'clear'}
          onCancel={() => setConfirmClear(false)}
          onConfirm={doClear}
        >
          <p>The environment variable, if one is set, will be used instead.</p>
        </ConfirmModal>
      )}
    </div>
  );
}

export default ProviderKeyField;
