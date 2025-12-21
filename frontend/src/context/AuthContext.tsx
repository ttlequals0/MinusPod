import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { getAuthStatus, login as apiLogin, logout as apiLogout, AuthStatus } from '../api/auth';

interface AuthContextType {
  isLoading: boolean;
  isAuthenticated: boolean;
  isPasswordSet: boolean;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refreshStatus: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [authStatus, setAuthStatus] = useState<AuthStatus>({
    passwordSet: false,
    authenticated: true,
  });

  const refreshStatus = async () => {
    try {
      const status = await getAuthStatus();
      setAuthStatus(status);
    } catch (error) {
      console.error('Failed to get auth status:', error);
      // On error, assume not authenticated
      setAuthStatus({ passwordSet: true, authenticated: false });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    refreshStatus();
  }, []);

  const login = async (password: string): Promise<boolean> => {
    try {
      const response = await apiLogin(password);
      if (response.authenticated) {
        setAuthStatus(prev => ({ ...prev, authenticated: true }));
        return true;
      }
      return false;
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
