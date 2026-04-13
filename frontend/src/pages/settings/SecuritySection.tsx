import { useState, type FormEvent } from 'react';
import { rotateMasterPassphrase } from '../../api/providers';
import CollapsibleSection from '../../components/CollapsibleSection';
import { setPassword, removePassword } from '../../api/auth';

interface SecuritySectionProps {
  cryptoReady?: boolean;
  isPasswordSet: boolean;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<void>;
}

function SecuritySection({ isPasswordSet, logout, refreshStatus, cryptoReady = false }: SecuritySectionProps) {
  const [oldPassphrase, setOldPassphrase] = useState('');
  const [newPassphrase, setNewPassphrase] = useState('');
  const [confirmPassphrase, setConfirmPassphrase] = useState('');
  const [rotateError, setRotateError] = useState<string | null>(null);
  const [rotateSuccess, setRotateSuccess] = useState<string | null>(null);
  const [isRotating, setIsRotating] = useState(false);

  async function handleRotatePassphrase(e: FormEvent) {
    e.preventDefault();
    setRotateError(null); setRotateSuccess(null);
    if (!oldPassphrase || !newPassphrase) {
      setRotateError('Both current and new passphrase are required.');
      return;
    }
    if (newPassphrase.length < 12) {
      setRotateError('New passphrase must be at least 12 characters.');
      return;
    }
    if (newPassphrase !== confirmPassphrase) {
      setRotateError('New passphrase confirmation does not match.');
      return;
    }
    if (!window.confirm(
      'After rotation you MUST update MINUSPOD_MASTER_PASSPHRASE in the container environment and restart the container. ' +
      'If you forget, stored keys will be unreadable on next restart. ' +
      'If you lose the new passphrase, the stored keys are unrecoverable. Continue?',
    )) return;
    setIsRotating(true);
    try {
      const r = await rotateMasterPassphrase(oldPassphrase, newPassphrase);
      setRotateSuccess(`Re-encrypted ${r.rotated} stored key${r.rotated === 1 ? '' : 's'}. Update the env var now.`);
      setOldPassphrase(''); setNewPassphrase(''); setConfirmPassphrase('');
    } catch (err) {
      setRotateError(err instanceof Error ? err.message : 'Rotation failed');
    } finally {
      setIsRotating(false);
    }
  }

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

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

    if (newPassword && newPassword.length < 8) {
      setPasswordError('Password must be at least 8 characters');
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
            className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors"
          >
            Logout
          </button>
        )}
      </div>

      {!isPasswordSet && (
        <div className="mb-4 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
          <p className="text-sm text-yellow-600 dark:text-yellow-400">
            This application has no password protection. Anyone with network access can view and modify data.
          </p>
        </div>
      )}

      <form onSubmit={handlePasswordSubmit} className="space-y-4">
        {isPasswordSet && (
          <div>
            <label htmlFor="currentPassword" className="block text-sm font-medium text-foreground mb-2">
              Current Password
            </label>
            <input
              type="password"
              id="currentPassword"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        )}

        <div>
          <label htmlFor="newPassword" className="block text-sm font-medium text-foreground mb-2">
            {isPasswordSet ? 'New Password' : 'Set Password'}
          </label>
          <input
            type="password"
            id="newPassword"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder={isPasswordSet ? 'Leave empty to remove password' : 'Minimum 8 characters'}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div>
          <label htmlFor="confirmPassword" className="block text-sm font-medium text-foreground mb-2">
            Confirm Password
          </label>
          <input
            type="password"
            id="confirmPassword"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        {passwordError && (
          <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
            {passwordError}
          </div>
        )}

        {passwordSuccess && (
          <div className="p-3 rounded-lg bg-green-500/10 text-green-600 dark:text-green-400 text-sm">
            {passwordSuccess}
          </div>
        )}

        <button
          type="submit"
          disabled={isChangingPassword || (!isPasswordSet && !newPassword)}
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {isChangingPassword
            ? 'Saving...'
            : isPasswordSet
            ? newPassword
              ? 'Change Password'
              : 'Remove Password'
            : 'Set Password'}
        </button>
      </form>

      <div className="mt-6 pt-6 border-t border-border">
        <h3 className="text-base font-semibold text-foreground mb-1">Provider Key Encryption</h3>
        <p className="text-sm text-muted-foreground mb-4">
          Rotate the <code className="font-mono">MINUSPOD_MASTER_PASSPHRASE</code> used to encrypt provider API keys.
          Re-encrypts every stored key under a new passphrase + new salt in a single transaction.
        </p>

        {!cryptoReady ? (
          <p className="text-sm text-muted-foreground">
            Set <code className="font-mono">MINUSPOD_MASTER_PASSPHRASE</code> in the container environment first.
            Rotation is only available once the encrypted store is initialized.
          </p>
        ) : (
          <form onSubmit={handleRotatePassphrase} className="space-y-3">
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300 space-y-1">
              <p>After clicking Rotate, update <code className="font-mono">MINUSPOD_MASTER_PASSPHRASE</code> in your container environment to the new value and restart the container.</p>
              <p>Other Gunicorn workers keep the old key cached until restart, so stored keys may fail to decrypt in the meantime.</p>
              <p>Lose the new passphrase and the stored keys are unrecoverable. Back it up first.</p>
            </div>

            <div>
              <label htmlFor="oldPassphrase" className="block text-sm font-medium text-foreground mb-1">
                Current passphrase
              </label>
              <input
                id="oldPassphrase"
                type="password"
                autoComplete="off"
                value={oldPassphrase}
                onChange={(e) => setOldPassphrase(e.target.value)}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
            </div>

            <div>
              <label htmlFor="newPassphrase" className="block text-sm font-medium text-foreground mb-1">
                New passphrase
              </label>
              <input
                id="newPassphrase"
                type="password"
                autoComplete="off"
                value={newPassphrase}
                onChange={(e) => setNewPassphrase(e.target.value)}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
            </div>

            <div>
              <label htmlFor="confirmPassphrase" className="block text-sm font-medium text-foreground mb-1">
                Confirm new passphrase
              </label>
              <input
                id="confirmPassphrase"
                type="password"
                autoComplete="off"
                value={confirmPassphrase}
                onChange={(e) => setConfirmPassphrase(e.target.value)}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
            </div>

            {rotateError && <p className="text-sm text-destructive">{rotateError}</p>}
            {rotateSuccess && <p className="text-sm text-green-600 dark:text-green-400">{rotateSuccess}</p>}

            <button
              type="submit"
              disabled={isRotating}
              className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
            >
              {isRotating ? 'Rotating...' : 'Rotate Master Passphrase'}
            </button>
          </form>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default SecuritySection;
