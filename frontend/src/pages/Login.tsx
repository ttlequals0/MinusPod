import { useState, FormEvent } from 'react';
import { useNavigate, Navigate } from 'react-router';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { takeLoginRedirect } from '../utils/loginRedirect';
import LoadingSpinner from '../components/LoadingSpinner';
import { btnLg, btnPrimary } from '../components/buttonStyles';
import Checkbox from '../components/Checkbox';
import { inputBase } from '../components/fieldStyles';
import { uiUrl } from '../urlPrefix';

function Login() {
  const navigate = useNavigate();
  const { login, isAuthenticated, isPasswordSet, isLoading, refreshStatus } = useAuth();
  const { theme } = useTheme();
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Show a spinner (not the login form) while auth status is still resolving,
  // so a returning user does not see a flash of the form before the redirect.
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  // Guard: wait for auth status (!isLoading) and skip during submit (!isSubmitting): handleSubmit owns
  // navigation once it starts, and firing the guard first would double-consume takeLoginRedirect.
  if (!isLoading && !isSubmitting && (isAuthenticated || !isPasswordSet)) {
    return <Navigate to={takeLoginRedirect()} replace />;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    const result = await login(password, rememberMe);
    if (result.ok) {
      // Verify the cookie persisted: a Secure cookie over plain HTTP is silently discarded, so the POST looks
      // successful but the next request is unauthenticated. refreshStatus() re-fetches the real state without
      // stale closure values.
      try {
        const realStatus = await refreshStatus();
        if (!realStatus.authenticated) {
          setError(
            'Signed in, but the browser rejected the session cookie. ' +
            'Over plain HTTP set SESSION_COOKIE_SECURE=false or use HTTPS.'
          );
          setIsSubmitting(false);
          return;
        }
        // Keep isSubmitting=true through navigation so the guard cannot fire
        // and consume takeLoginRedirect() a second time before unmount.
        navigate(takeLoginRedirect(), { replace: true });
        return;
      } catch {
        setError('Login failed. Please try again.');
      }
    } else if (result.kind === 'rateLimited') {
      const mins = Math.max(1, Math.ceil(result.retryAfterSeconds / 60));
      setError(`Too many sign-in attempts. Try again in ${mins} ${mins === 1 ? 'minute' : 'minutes'}.`);
    } else if (result.kind === 'invalid') {
      setError('Invalid password');
      setPassword('');
    } else {
      setError('Login failed. Please try again.');
    }
    setIsSubmitting(false);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm">
        <div className="bg-card border border-border rounded-lg p-8">
          <div className="text-center mb-8">
            <img
              src={uiUrl(theme === 'dark' ? '/logo-dark.svg' : '/logo.svg')}
              alt="MinusPod"
              className="h-10 mx-auto"
            />
            <p className="text-sm text-muted-foreground mt-2">Enter password to continue</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label htmlFor="password" className="sr-only">
                Password
              </label>
              <input
                type="password"
                id="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                required
                autoFocus
                className={`${inputBase} w-full`}
              />
            </div>

            <Checkbox
              checked={rememberMe}
              onChange={setRememberMe}
              label="Remember me on this device"
              className="pointer-coarse:min-h-11"
            />

            {error && (
              <div role="alert" className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm text-center [text-wrap:balance]">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={isSubmitting || !password}
              className={`${btnLg} ${btnPrimary} w-full`}
            >
              {isSubmitting ? 'Signing in...' : 'Sign In'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

export default Login;
