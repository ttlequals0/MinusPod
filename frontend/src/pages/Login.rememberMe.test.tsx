import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Login from './Login';

const mockNavigate = vi.fn();
vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
  Navigate: ({ to }: { to: string }) => <div data-testid="navigate-sentinel" data-to={to} />,
}));
vi.mock('../context/ThemeContext', () => ({ useTheme: () => ({ theme: 'light' }) }));

const mockLogin = vi.fn();
const mockRefreshStatus = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let authState: any;
vi.mock('../context/AuthContext', () => ({ useAuth: () => authState }));

beforeEach(() => {
  mockNavigate.mockReset(); mockLogin.mockReset(); mockRefreshStatus.mockReset();
  sessionStorage.clear();
  authState = { isLoading: false, isAuthenticated: false, isPasswordSet: true,
    login: mockLogin, refreshStatus: mockRefreshStatus, logout: vi.fn() };
});

async function submit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByPlaceholderText('Password'), 'pw');
  await user.click(screen.getByRole('button', { name: /sign in/i }));
}

describe('Login remember me', () => {
  it('passes rememberMe=true to login when the checkbox is ticked', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ ok: true });
    mockRefreshStatus.mockResolvedValue({ authenticated: true, passwordSet: true });
    render(<Login />);
    await user.type(screen.getByPlaceholderText('Password'), 'pw');
    await user.click(screen.getByLabelText('Remember me on this device'));
    await user.click(screen.getByRole('button', { name: /sign in/i }));
    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith('pw', true));
  });

  it('defaults rememberMe=false when the checkbox is untouched', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ ok: true });
    mockRefreshStatus.mockResolvedValue({ authenticated: true, passwordSet: true });
    render(<Login />);
    await submit(user);
    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith('pw', false));
  });

  it('does not persist the checkbox state', async () => {
    const user = userEvent.setup();
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    render(<Login />);
    await user.click(screen.getByLabelText('Remember me on this device'));
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it('shows the lockout message with rounded-up minutes (plural) as an alert', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ ok: false, kind: 'rateLimited', retryAfterSeconds: 150 });
    render(<Login />);
    await submit(user);
    await waitFor(() => expect(
      screen.getByRole('alert').textContent).toBe('Too many sign-in attempts. Try again in 3 minutes.'));
  });

  it('uses the singular minute for 60 seconds or less', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ ok: false, kind: 'rateLimited', retryAfterSeconds: 40 });
    render(<Login />);
    await submit(user);
    await waitFor(() => expect(
      screen.queryByText('Too many sign-in attempts. Try again in 1 minute.')).not.toBeNull());
  });

  it('shows "Invalid password" on an invalid result', async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue({ ok: false, kind: 'invalid' });
    render(<Login />);
    await submit(user);
    await waitFor(() => expect(screen.queryByText('Invalid password')).not.toBeNull());
  });
});
