import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import { setPassword, removePassword, AuthStatus } from '../../api/auth';
import { getSettings, updateSettings } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { RemovableChip } from '../../components/RemovableChip';
import { btnBase, btnLg, btnPrimary, btnSecondary, btnOutline } from '../../components/buttonStyles';
import { inputBase, labelBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';

const MIN_PASSWORD_LENGTH = 12;

interface SecuritySectionProps {
  cryptoReady?: boolean;
  isPasswordSet: boolean;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<AuthStatus>;
  plaintextSecretsCount?: number;
}

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

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  useUnsavedChanges(!!(agentInput || currentPassword || newPassword || confirmPassword));

  const handleLogout = async () => {
    await logout();
    window.location.href = '/ui/login';
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
        {blockedAgents.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-2">
            {blockedAgents.map((agent) => (
              <RemovableChip key={agent} label={agent} onRemove={() => removeBlockedAgent(agent)}
                disabled={agentsMutation.isPending} />
            ))}
          </div>
        )}
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
