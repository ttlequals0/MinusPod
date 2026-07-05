import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { getAuthStatus, login as apiLogin, logout as apiLogout, AuthStatus } from '../api/auth';

interface AuthContextType {
  isLoading: boolean;
  isAuthenticated: boolean;
  isPasswordSet: boolean;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<AuthStatus>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  // Start as not-authenticated so the Login guard does not fire before the
  // real /auth/status response arrives (issue #460 root cause 1).
  const [authStatus, setAuthStatus] = useState<AuthStatus>({
    passwordSet: false,
    authenticated: false,
  });

  const refreshStatus = async (): Promise<AuthStatus> => {
    try {
      const status = await getAuthStatus();
      setAuthStatus(status);
      return status;
    } catch (error) {
      console.error('Failed to get auth status:', error);
      // On error, assume not authenticated
      const fallback: AuthStatus = { passwordSet: true, authenticated: false };
      setAuthStatus(fallback);
      return fallback;
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    // One-shot bootstrap fetch on mount; this is the "subscribe to external
    // system" pattern the rule docs allow, just expressed as a single fire.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshStatus();
  }, []);

  const login = async (password: string): Promise<boolean> => {
    try {
      const response = await apiLogin(password);
      // Return the server's verdict without mutating auth state.
      // refreshStatus() (called by Login after this) is the sole source of
      // truth for isAuthenticated, so we don't optimistically set it here.
      return response.authenticated;
    } catch {
      return false;
    }
  };

  const logout = async () => {
    try {
      await apiLogout();
    } catch (error) {
      console.error('Logout error:', error);
    } finally {
      setAuthStatus(prev => ({ ...prev, authenticated: false }));
    }
  };

  return (
    <AuthContext.Provider
      value={{
        isLoading,
        isAuthenticated: authStatus.authenticated,
        isPasswordSet: authStatus.passwordSet,
        login,
        logout,
        refreshStatus,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
