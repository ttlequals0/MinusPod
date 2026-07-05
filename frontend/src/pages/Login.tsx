import { useState, FormEvent } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import { takeLoginRedirect } from '../utils/loginRedirect';

function Login() {
  const navigate = useNavigate();
  const { login, isAuthenticated, isPasswordSet, isLoading, refreshStatus } = useAuth();
  const { theme } = useTheme();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Guard: wait for auth status (!isLoading) and suppress during submit
  // (!isSubmitting -- handleSubmit owns navigation once it starts, and
  // refreshStatus() inside it will have set isAuthenticated by the time
  // navigate() is called; firing the guard first double-consumes takeLoginRedirect).
  if (!isLoading && !isSubmitting && (isAuthenticated || !isPasswordSet)) {
    return <Navigate to={takeLoginRedirect()} replace />;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const success = await login(password);
      if (success) {
        // Verify the cookie actually persisted: a Secure cookie over plain HTTP
        // is silently discarded by the browser, so the POST appears successful
        // but the next request is unauthenticated. refreshStatus() re-fetches
        // the real state and returns it without relying on stale closure values.
        const realStatus = await refreshStatus();
        if (!realStatus.authenticated) {
          setError(
            'Signed in, but the browser rejected the session cookie. ' +
            'Over plain HTTP set SESSION_COOKIE_SECURE=false or use HTTPS.'
          );
          return;
        }
        navigate(takeLoginRedirect(), { replace: true });
      } else {
        setError('Invalid password');
        setPassword('');
      }
    } catch {
      setError('Login failed. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm">
        <div className="bg-card border border-border rounded-lg shadow-lg p-8">
          <div className="text-center mb-8">
            <img
              src={theme === 'dark' ? '/ui/logo-dark.svg' : '/ui/logo.svg'}
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
                className="w-full px-4 py-3 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring"
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm text-center">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={isSubmitting || !password}
              className="w-full px-4 py-3 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors font-medium"
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
