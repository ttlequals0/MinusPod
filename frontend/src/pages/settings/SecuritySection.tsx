import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import { setPassword, removePassword, AuthStatus } from '../../api/auth';
import { getSettings, updateSettings } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import { tableBody, tableCard, tableHead, tableRow, thBase } from '../../components/tableStyles';
import { btnBase, btnLg, btnPrimary, btnSecondary, btnOutline, btnDestructiveOutline } from '../../components/buttonStyles';
import { inputBase, labelBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';
import { uiUrl } from '../../urlPrefix';
import { DemoDisabled } from '../../components/DemoDisabled';
import { limitsOf } from '../../utils/settingLimits';

const MIN_PASSWORD_LENGTH = 12;
// Ranges and defaults used until the server reports them (older servers and fixtures omit them).
const HOURS_FALLBACK = { min: 1, max: 8760 };
const DAYS_FALLBACK = { min: 1, max: 365 };
const DEFAULT_HOURS = 24;
const DEFAULT_DAYS = 30;

const isInRange = (v: number | null | undefined, { min, max }: { min: number; max: number }): v is number =>
  v != null && Number.isInteger(v) && v >= min && v <= max;

function SessionLengthField({ id, label, unit, help, value, range, valid, disabled, onChange }: {
  id: string;
  label: string;
  unit: string;
  help: string;
  value: number | null;
  range: { min: number; max: number };
  valid: boolean;
  disabled: boolean;
  onChange: (v: number | null) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className={labelBase}>{label}</label>
      <DraftNumberInput
        id={id}
        value={value}
        fallback={null}
        parse={parseOptionalNumber}
        onChange={onChange}
        min={range.min}
        max={range.max}
        step={1}
        disabled={disabled}
        ariaInvalid={!valid}
        ariaDescribedBy={`${id}-help${!valid ? ` ${id}-error` : ''}`}
        className="w-full aria-invalid:border-destructive aria-invalid:ring-2 aria-invalid:ring-destructive"
      />
      <p id={`${id}-help`} className="mt-1 text-sm text-muted-foreground">{help}</p>
      {!valid && (
        <p id={`${id}-error`} role="alert" className="mt-1 text-sm text-destructive">
          Enter a whole number of {unit} from {range.min} to {range.max}.
        </p>
      )}
    </div>
  );
}

interface SecuritySectionProps {
  cryptoReady?: boolean;
  isPasswordSet: boolean;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<AuthStatus>;
  plaintextSecretsCount?: number;
}

const agentHead = `${thBase} px-3 py-3 text-left`;
const agentCell = 'px-3 py-3 text-sm';

function SecuritySection({
  isPasswordSet,
  logout,
  refreshStatus,
  cryptoReady = false,
  plaintextSecretsCount = 0,
}: SecuritySectionProps) {
  const queryClient = useQueryClient();

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const blockedAgents = settings?.jitBlockedUserAgents?.value ?? [];
  const [addingAgent, setAddingAgent] = useState(false);
  const [agentInput, setAgentInput] = useState('');
  const [agentError, setAgentError] = useState<string | null>(null);

  const agentsMutation = useMutation({
    mutationFn: (agents: string[]) => updateSettings({ jitBlockedUserAgents: agents }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const addBlockedAgent = () => {
    const pattern = agentInput.trim();
    if (!pattern) return;
    if (blockedAgents.includes(pattern)) {
      setAgentInput('');
      setAddingAgent(false);
      return;
    }
    setAgentError(null);
    agentsMutation.mutate([...blockedAgents, pattern], {
      onSuccess: () => {
        setAgentInput('');
        setAddingAgent(false);
      },
      onError: (e) => setAgentError(getErrorMessage(e, 'Could not add agent.')),
    });
  };

  const removeBlockedAgent = (agent: string) => {
    setAgentError(null);
    agentsMutation.mutate(blockedAgents.filter((a) => a !== agent), {
      onError: (e) => setAgentError(getErrorMessage(e, 'Could not remove agent.')),
    });
  };

  const removeAgentButton = (agent: string) => (
    <button
      type="button"
      onClick={() => removeBlockedAgent(agent)}
      disabled={agentsMutation.isPending}
      aria-label={`Remove ${agent}`}
      className={`${btnBase} ${btnDestructiveOutline} shrink-0`}
    >
      Remove
    </button>
  );

  const savedHours = settings?.sessionLifetimeHours?.value ?? DEFAULT_HOURS;
  const savedDays = settings?.sessionRememberDays?.value ?? DEFAULT_DAYS;
  // undefined means untouched; null means cleared or not a number.
  const [hoursDraft, setHoursDraft] = useState<number | null | undefined>(undefined);
  const [daysDraft, setDaysDraft] = useState<number | null | undefined>(undefined);
  const [sessionSaved, setSessionSaved] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const hoursRange = limitsOf(settings?.sessionLifetimeHours, HOURS_FALLBACK);
  const daysRange = limitsOf(settings?.sessionRememberDays, DAYS_FALLBACK);
  const hours = hoursDraft === undefined ? savedHours : hoursDraft;
  const days = daysDraft === undefined ? savedDays : daysDraft;
  const hoursValid = isInRange(hours, hoursRange);
  const daysValid = isInRange(days, daysRange);
  const sessionDirty = hours !== savedHours || days !== savedDays;

  const sessionMutation = useMutation({
    mutationFn: (payload: { sessionLifetimeHours: number; sessionRememberDays: number }) => updateSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['settings'] });
      setHoursDraft(undefined);
      setDaysDraft(undefined);
      setSessionSaved(true);
    },
    onError: (e) => setSessionError(getErrorMessage(e, 'Could not save session lengths.')),
  });

  const saveSessionLengths = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isInRange(hours, hoursRange) || !isInRange(days, daysRange) || sessionMutation.isPending) return;
    setSessionError(null);
    setSessionSaved(false);
    sessionMutation.mutate({ sessionLifetimeHours: hours, sessionRememberDays: days });
  };

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  useUnsavedChanges(!!(agentInput || currentPassword || newPassword || confirmPassword) || sessionDirty);

  const handleLogout = async () => {
    await logout();
    window.location.href = uiUrl('/login');
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);

    if (newPassword !== confirmPassword) {
      setPasswordError('Passwords do not match');
      return;
    }

    if (newPassword && newPassword.length < MIN_PASSWORD_LENGTH) {
      setPasswordError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`);
      return;
    }

    setIsChangingPassword(true);
    try {
      if (newPassword) {
        await setPassword(newPassword, currentPassword);
        setPasswordSuccess(isPasswordSet ? 'Password changed successfully' : 'Password set successfully');
      } else {
        await removePassword(currentPassword);
        setPasswordSuccess('Password protection removed');
      }
      await refreshStatus();
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (error) {
      setPasswordError((error as Error).message);
    } finally {
      setIsChangingPassword(false);
    }
  };

  return (
    <CollapsibleSection
      title="Security"
      subtitle={isPasswordSet ? 'Password protection is enabled' : 'No password set - app is publicly accessible'}
    >
      <div className="flex justify-end mb-4">
        {isPasswordSet && (
          <button
            onClick={handleLogout}
            className={`${btnBase} ${btnSecondary}`}
          >
            Logout
          </button>
        )}
      </div>

      {!isPasswordSet && (
        <div className="mb-4 p-3 rounded-lg bg-warning/10 border border-warning/20">
          <p className="text-sm text-warning">
            {cryptoReady
              ? 'The master passphrase encrypts stored API keys but does not restrict access to this app; anyone with network access still has full control. Set a password below to protect it.'
              : 'This instance has no password, so anyone with network access has full control: they can read everything, change settings, delete feeds, and download a complete database backup. Set a password below to protect it.'}
          </p>
        </div>
      )}

      <form onSubmit={handlePasswordSubmit} className="space-y-4">
        {/* Single-user app: a hidden username keeps password managers and
            Chrome's accessibility check happy without a visible field. */}
        <input type="text" name="username" autoComplete="username"
               value="minuspod" readOnly hidden />
        {isPasswordSet && (
          <div>
            <label htmlFor="currentPassword" className={labelBase}>
              Current password
            </label>
            <input
              type="password"
              id="currentPassword"
              disabled={isChangingPassword}
              aria-describedby={passwordError ? "password-error" : undefined}
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              className={`${inputBase} w-full`}
            />
          </div>
        )}

        <div>
          <label htmlFor="newPassword" className={labelBase}>
            {isPasswordSet ? 'New password' : 'Set password'}
          </label>
          <input
            type="password"
            id="newPassword"
            aria-invalid={!!passwordError && !!newPassword && newPassword.length < MIN_PASSWORD_LENGTH}
            disabled={isChangingPassword}
            aria-describedby={passwordError ? "password-error" : undefined}
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder={isPasswordSet ? 'Leave empty to remove password' : `Minimum ${MIN_PASSWORD_LENGTH} characters`}
            className={`${inputBase} w-full`}
          />
        </div>

        <div>
          <label htmlFor="confirmPassword" className={labelBase}>
            Confirm password
          </label>
          <input
            type="password"
            id="confirmPassword"
            aria-invalid={!!passwordError && newPassword !== confirmPassword}
            disabled={isChangingPassword}
            aria-describedby={passwordError ? "password-error" : undefined}
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className={`${inputBase} w-full`}
          />
        </div>

        {passwordError && (
          <div id="password-error" role="alert" className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
            {passwordError}
          </div>
        )}

        {passwordSuccess && (
          <div className="p-3 rounded-lg bg-success/10 text-success text-sm">
            {passwordSuccess}
          </div>
        )}

        <DemoDisabled>
          <button
            type="submit"
            disabled={isChangingPassword || (!isPasswordSet && !newPassword)}
            className={`${btnLg} ${btnPrimary}`}
          >
            {isChangingPassword
              ? 'Saving...'
              : isPasswordSet
              ? newPassword
                ? 'Change password'
                : 'Remove password'
              : 'Set password'}
          </button>
        </DemoDisabled>
      </form>

      <form onSubmit={saveSessionLengths} noValidate className="mt-6 pt-6 border-t border-border">
        <h3 className={`${subHeading} mb-1`}>Session length</h3>
        <p className="text-sm text-muted-foreground mb-4">
          How long a sign-in stays valid before the password is asked for again.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SessionLengthField
            id="sessionLifetimeHours"
            label="Session length (hours)"
            unit="hours"
            help="How long a normal sign-in stays valid, counted from its last use."
            value={hours}
            range={hoursRange}
            valid={hoursValid}
            disabled={sessionMutation.isPending}
            onChange={(v) => { setHoursDraft(v); setSessionSaved(false); }}
          />
          <SessionLengthField
            id="sessionRememberDays"
            label="Remember me length (days)"
            unit="days"
            help="Applies only to sign-ins that tick the Remember me box; counted from last use."
            value={days}
            range={daysRange}
            valid={daysValid}
            disabled={sessionMutation.isPending}
            onChange={(v) => { setDaysDraft(v); setSessionSaved(false); }}
          />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <DemoDisabled>
            <button
              type="submit"
              disabled={!sessionDirty || !hoursValid || !daysValid || sessionMutation.isPending}
              className={`${btnLg} ${btnPrimary}`}
            >
              {sessionMutation.isPending ? 'Saving...' : 'Save session lengths'}
            </button>
          </DemoDisabled>
          {sessionSaved && (
            <span role="status" className="text-sm text-success">Session lengths saved.</span>
          )}
        </div>
        {sessionError && (
          <p role="alert" className="mt-2 text-sm text-destructive">{sessionError}</p>
        )}
      </form>

      <div className="mt-6 pt-6 border-t border-border">
        <h3 className={`${subHeading} mb-1`}>Provider key encryption</h3>
        <p className="text-sm text-muted-foreground mb-4">
          {cryptoReady
            ? <>Provider API keys are encrypted with <code className="font-mono">MINUSPOD_MASTER_PASSPHRASE</code>.</>
            : <>Set <code className="font-mono">MINUSPOD_MASTER_PASSPHRASE</code> in the container environment to encrypt provider API keys.</>}
        </p>

        {plaintextSecretsCount > 0 && (
          <div className="mb-4 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
            {plaintextSecretsCount} provider key
            {plaintextSecretsCount === 1 ? '' : 's'} still stored as plaintext.
            {cryptoReady
              ? ' Restart the server or re-save the key to encrypt it at rest.'
              : ' Set MINUSPOD_MASTER_PASSPHRASE in the container environment and restart; the startup migration will re-encrypt them.'}
          </div>
        )}

        {cryptoReady && <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning space-y-2">
          <p>Stop every MinusPod worker before rotating the master passphrase.</p>
          <p>Run <code className="font-mono break-all">python scripts/rotate_master_passphrase.py</code>, update the container environment, then restart all workers together.</p>
          <p>Keep an encrypted backup and the current passphrase until the restarted service can read every stored key.</p>
        </div>}
      </div>

      <div className="mt-6 pt-6 border-t border-border">
        <h3 className={`${subHeading} mb-1`}>Agents that skip processing</h3>
        <p className="text-sm text-muted-foreground mb-4">
          Agents listed here get the original audio instead of triggering processing. Case-insensitive, matches anywhere in the agent string. Start a pattern with ^ to match only the beginning, for example ^atc/.
        </p>
        <div className="mb-2 space-y-2">
          {blockedAgents.length === 0 ? (
            <p className="text-sm text-muted-foreground">No agents yet.</p>
          ) : (
            <>
              <div className={`hidden sm:block ${tableCard}`}>
                <table className="w-full">
                  <thead className={tableHead}>
                    <tr>
                      <th className={agentHead}>Agent</th>
                      <th className={agentHead}><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody className={tableBody}>
                    {blockedAgents.map((agent) => (
                      <tr key={agent} className={tableRow}>
                        <td className={`${agentCell} min-w-0 break-all font-mono text-foreground`}>{agent}</td>
                        <td className={`${agentCell} text-right whitespace-nowrap`}>{removeAgentButton(agent)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="sm:hidden space-y-2">
                {blockedAgents.map((agent) => (
                  <div key={agent} className="bg-muted/40 rounded-lg p-4 flex items-center justify-between gap-3">
                    <span className="min-w-0 break-all font-mono text-sm text-foreground">{agent}</span>
                    {removeAgentButton(agent)}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!addingAgent ? (
            <button
              type="button"
              onClick={() => setAddingAgent(true)}
              disabled={agentsMutation.isPending}
              className={`${btnBase} ${btnOutline}`}
            >
              + Add agent
            </button>
          ) : (
            <>
              <input
                type="text"
                autoFocus
                value={agentInput}
                onChange={(e) => setAgentInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addBlockedAgent();
                  }
                }}
                placeholder="^atc/"
                aria-label="New blocked agent pattern"
                aria-describedby={agentError ? "blocked-agent-error" : undefined}
                maxLength={200}
                className={`${inputBase} flex-1 min-w-0`}
              />
              <button
                type="button"
                onClick={addBlockedAgent}
                disabled={agentsMutation.isPending || !agentInput.trim()}
                className={`${btnBase} ${btnOutline}`}
              >
                Add
              </button>
              <button
                type="button"
                onClick={() => {
                  setAddingAgent(false);
                  setAgentInput('');
                  setAgentError(null);
                }}
                className={`${btnBase} ${btnOutline}`}
              >
                Cancel
              </button>
            </>
          )}
        </div>
        {agentError && (
          <p id="blocked-agent-error" role="alert" className="mt-2 text-sm text-destructive">{agentError}</p>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default SecuritySection;
