import { useState } from 'react';
import { getErrorMessage } from '../../api/client';
import type { ConnectionTestResult } from '../../api/providers';
import { btnBase, btnOutline } from '../../components/buttonStyles';

// A result only describes the values it was tested with. Parents pass a
// `key` built from those values, so React remounts this component (clearing
// the shown result) the moment any tested value changes -- a stale green
// "Connected" never sits next to an untested value.
interface ConnectionTestButtonProps {
  onTest: () => Promise<ConnectionTestResult>;
  busyHint?: string;
  disabled?: boolean;
  disabledReason?: string;
}

function ConnectionTestButton({
  onTest, busyHint, disabled, disabledReason,
}: ConnectionTestButtonProps) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConnectionTestResult | null>(null);

  async function handleTest() {
    setBusy(true);
    setResult(null);
    try {
      setResult(await onTest());
    } catch (e) {
      setResult({
        ok: false,
        reachable: false,
        detail: getErrorMessage(e, 'Could not test connection.'),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          disabled={busy || disabled}
          onClick={handleTest}
          title={disabled ? disabledReason : undefined}
          className={`${btnBase} ${btnOutline}`}
        >
          {busy ? 'Testing...' : 'Test connection'}
        </button>
        {busy && busyHint && (
          <span className="text-sm text-muted-foreground">{busyHint}</span>
        )}
      </div>
      {result && (
        <p
          className={`mt-2 text-sm ${
            result.ok
              ? 'text-success'
              : result.reachable
                ? 'text-warning'
                : 'text-destructive'
          }`}
          role="status"
        >
          {result.detail}
        </p>
      )}
    </>
  );
}

export default ConnectionTestButton;
