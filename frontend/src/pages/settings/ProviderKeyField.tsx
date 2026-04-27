import { useState } from 'react';
import type { ProviderName, ProviderStatus } from '../../api/providers';

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
  db:   { bg: 'bg-green-500/10 text-green-600 dark:text-green-400', dot: 'bg-green-500', text: 'Stored encrypted' },
  env:  { bg: 'bg-amber-500/10 text-amber-600 dark:text-amber-400', dot: 'bg-amber-500', text: 'Using env fallback' },
  none: { bg: 'bg-muted text-muted-foreground', dot: 'bg-muted-foreground/60', text: 'Not set' },
} as const;

function StatusChip({ source }: { source: ProviderStatus['source'] }) {
  const c = CHIP[source];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${c.bg}`}>
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
  const [error, setError] = useState<string | null>(null);

  const showActions = status.source === 'db' || draft.length > 0;

  async function handleSave() {
    if (!draft) return;
    setBusy('save'); setError(null); setTestResult(null);
    try { await onSave(provider, draft); setDraft(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'Save failed'); }
    finally { setBusy(null); }
  }

  async function handleClear() {
    if (!window.confirm(`Remove stored ${provider} key? The environment variable (if any) will be used instead.`)) return;
    setBusy('clear'); setError(null); setTestResult(null);
    try { await onClear(provider); setDraft(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'Clear failed'); }
    finally { setBusy(null); }
  }

  async function handleTest() {
    setBusy('test'); setTestResult(null);
    try {
      const r = await onTest(provider);
      setTestResult({ ok: r.ok, msg: r.ok ? 'OK' : (r.error || 'failed') });
    } catch (e) {
      setTestResult({ ok: false, msg: e instanceof Error ? e.message : 'failed' });
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
            className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {busy === 'save' ? 'Saving...' : 'Save'}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={handleTest}
            className="px-3 py-1.5 rounded-md border border-border text-sm font-medium hover:bg-secondary disabled:opacity-50"
          >
            {busy === 'test' ? 'Testing...' : 'Test'}
          </button>
          {status.source === 'db' && (
            <button
              type="button"
              disabled={busy !== null}
              onClick={handleClear}
              className="px-3 py-1.5 rounded-md border border-border text-sm font-medium text-destructive hover:bg-secondary disabled:opacity-50"
            >
              Clear
            </button>
          )}
          {testResult && (
            <span className={`text-sm ${testResult.ok ? 'text-green-600 dark:text-green-400' : 'text-destructive'}`}>
              {testResult.msg}
            </span>
          )}
        </div>
      )}
      {error && <p className="mt-1 text-sm text-destructive">{error}</p>}
    </div>
  );
}

export default ProviderKeyField;
