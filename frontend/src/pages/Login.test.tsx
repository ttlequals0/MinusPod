/**
 * Component tests for Login.tsx (issue #460).
 *
 * Covers the two loop-breaker conditions:
 *   1. !isLoading guard: form shown, no redirect while auth status is loading.
 *   2. !isSubmitting guard + no optimistic authenticated set: handleSubmit owns
 *      navigation; takeLoginRedirect is consumed exactly once.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Login from './Login';

// react-router-dom: capture navigate calls; render Navigate as a sentinel element.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  Navigate: ({ to }: { to: string }) => (
    <div data-testid="navigate-sentinel" data-to={to} />
  ),
}));

vi.mock('../context/ThemeContext', () => ({
  useTheme: () => ({ theme: 'light' }),
}));

// Mutable auth state object; reassigned in beforeEach and per-test.
const mockLogin = vi.fn();
const mockRefreshStatus = vi.fn();

let authState: {
  isLoading: boolean;
  isAuthenticated: boolean;
  isPasswordSet: boolean;
  login: typeof mockLogin;
  refreshStatus: typeof mockRefreshStatus;
  logout: ReturnType<typeof vi.fn>;
};

vi.mock('../context/AuthContext', () => ({
  useAuth: () => authState,
}));

beforeEach(() => {
  mockNavigate.mockReset();
  mockLogin.mockReset();
  mockRefreshStatus.mockReset();
  sessionStorage.clear();
  authState = {
    isLoading: false,
    isAuthenticated: false,
    isPasswordSet: true,
    login: mockLogin,
    refreshStatus: mockRefreshStatus,
    logout: vi.fn(),
  };
});

describe('Login guard: isLoading', () => {
  it('renders a spinner (not the form) and does not redirect while auth status is loading', () => {
    // Simulate the moment before /auth/status has resolved: isLoading=true,
    // isAuthenticated=true (as if a stale truthy value slipped through).
    // Without the !isLoading guard this would immediately redirect.
    authState = { ...authState, isLoading: true, isAuthenticated: true };

    const { container } = render(<Login />);

    // Spinner shown, login form withheld until status resolves (#464).
    expect(container.querySelector('.animate-spin')).not.toBeNull();
    expect(screen.queryByRole('button', { name: /sign in/i })).toBeNull();
    // No Navigate sentinel -- guard suppressed by isLoading.
    expect(screen.queryByTestId('navigate-sentinel')).toBeNull();
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});

describe('Login handleSubmit: cookie rejected', () => {
  it('shows cookie-rejected error and does not navigate when refreshStatus returns authenticated:false', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue(true);
    mockRefreshStatus.mockResolvedValue({ authenticated: false, passwordSet: true });

    render(<Login />);

    await user.type(screen.getByPlaceholderText('Password'), 'anypassword');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.queryByText(/browser rejected the session cookie/i)).not.toBeNull();
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});

describe('Login handleSubmit: success with redirect', () => {
  it('navigates to the sanitized redirect exactly once when refreshStatus returns authenticated:true', async () => {
    const user = userEvent.setup();
    // Seed redirect destination; takeLoginRedirect() will return '/feeds'.
    sessionStorage.setItem('loginRedirect', '/feeds');
    mockLogin.mockResolvedValue(true);
    mockRefreshStatus.mockResolvedValue({ authenticated: true, passwordSet: true });

    render(<Login />);

    await user.type(screen.getByPlaceholderText('Password'), 'correctpassword');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledTimes(1);
    });
    // Key consumed by navigate; not double-consumed by the guard.
    expect(mockNavigate).toHaveBeenCalledWith('/feeds', { replace: true });
  });
});

describe('Login handleSubmit: wrong password', () => {
  it('shows "Invalid password" and does not navigate on login failure', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue(false);

    render(<Login />);

    await user.type(screen.getByPlaceholderText('Password'), 'wrongpassword');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.queryByText('Invalid password')).not.toBeNull();
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});

describe('Login handleSubmit: deep-link guard suppression', () => {
  it('does not render the Navigate guard after successful login (isSubmitting stays true)', async () => {
    // This covers the race where finally { setIsSubmitting(false) } ran before
    // unmount and let the guard consume takeLoginRedirect() a second time.
    // After the fix, isSubmitting stays true through navigation, so the guard
    // cannot render <Navigate> and cannot call takeLoginRedirect() again.
    const user = userEvent.setup();
    sessionStorage.setItem('loginRedirect', '/settings');
    mockLogin.mockResolvedValue(true);
    mockRefreshStatus.mockResolvedValue({ authenticated: true, passwordSet: true });

    render(<Login />);

    await user.type(screen.getByPlaceholderText('Password'), 'correctpassword');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledTimes(1);
    });
    // navigate called with the stored path, never with '/'.
    expect(mockNavigate).toHaveBeenCalledWith('/settings', { replace: true });
    expect(mockNavigate).not.toHaveBeenCalledWith('/', expect.anything());
    // The Navigate sentinel must not appear -- guard must stay suppressed.
    expect(screen.queryByTestId('navigate-sentinel')).toBeNull();
  });
});
