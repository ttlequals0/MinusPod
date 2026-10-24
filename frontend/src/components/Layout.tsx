import { useCallback, useState } from 'react';
import { LogOut, Menu, Moon, Search, Sun, X } from 'lucide-react';
import { Outlet, Link, useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useTheme } from '../context/ThemeContext';
import { useAuth } from '../context/AuthContext';
import UpdateBanner from './UpdateBanner';
import QuickSearch, { useQuickSearchHotkey } from './QuickSearch';
import { getProcessingStatus } from '../api/status';
import { btnBase, btnPrimary, btnGhost, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { countPill, tint } from './badgeStyles';

const NAV_ITEMS: { to: string; label: string }[] = [
  { to: '/', label: 'Feeds' },
  { to: '/add', label: 'Add feed' },
  { to: '/patterns', label: 'Patterns' },
  { to: '/sponsors', label: 'Sponsors' },
  { to: '/history', label: 'History' },
  { to: '/stats', label: 'Stats' },
  { to: '/queue', label: 'Queue' },
  { to: '/settings', label: 'Settings' },
];
const COMPACT_HEADER_CONTROL_SIZE = 'h-11 w-11 xl:h-auto xl:w-auto';

interface NavLinkProps {
  to: string;
  label: string;
  active: boolean;
  onClick?: () => void;
  count?: number;
}

function NavLink({ to, label, active, onClick, count }: NavLinkProps) {
  return (
    <Link
      to={to}
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`flex min-h-11 items-center px-3 py-2 text-sm font-medium transition-colors xl:min-h-0 rounded-md ${focusRing} ${
        active
          ? 'bg-primary text-primary-foreground'
          : btnGhost
      }`}
    >
      {label}
      {count ? (
        <span className={`ml-1.5 ${countPill} ${
          active ? 'bg-primary-foreground/20 text-primary-foreground' : tint.primary
        }`}>
          {count}
        </span>
      ) : null}
    </Link>
  );
}

function isPathActive(pathname: string, to: string): boolean {
  if (to === '/') return pathname === '/';
  return pathname.startsWith(to);
}

function Layout() {
  const { theme, toggleTheme } = useTheme();
  const { logout, isPasswordSet } = useAuth();
  const location = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [searchSeed, setSearchSeed] = useState<string | null>(null);
  const queueActivity = useQuery({
    queryKey: ['processing-status'],
    queryFn: getProcessingStatus,
    enabled: false,
  });
  const queueCount = (queueActivity.data?.queueLength ?? 0)
    + (queueActivity.data?.jobs?.length ?? (queueActivity.data?.currentJob ? 1 : 0));
  const openSearch = useCallback((seed: string) => setSearchSeed(seed), []);
  const closeSearch = useCallback(() => setSearchSeed(null), []);
  useQuickSearchHotkey(openSearch);

  const handleLogout = async () => {
    await logout();
    window.location.href = '/ui/login';
  };
  // Reset the mobile menu when the route changes. React's docs explicitly
  // recommend the render-phase "adjust state on prop change" idiom over a
  // setState-in-useEffect, which the new react-compiler lint rule rejects.
  const [lastPath, setLastPath] = useState(location.pathname);
  if (location.pathname !== lastPath) {
    setLastPath(location.pathname);
    setMobileMenuOpen(false);
  }

  return (
    <div className="min-h-screen bg-background">
      <a
        href="#main"
        className={`sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 ${btnBase} ${btnPrimary}`}
      >
        Skip to content
      </a>
      <header className="sticky z-30 border-b border-border bg-card" style={{ top: 'var(--status-bar-height, 0px)' }}>
        <div className="safe-x safe-x-wide max-w-7xl mx-auto">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center gap-8">
              <Link to="/" className={`flex h-11 shrink-0 items-center ${focusRing} xl:h-auto`}>
                <img
                  src={theme === 'dark' ? '/ui/logo-dark.svg' : '/ui/logo.svg'}
                  alt="MinusPod"
                  className="h-7 xl:h-8"
                />
              </Link>
              <nav className="hidden shrink-0 gap-4 xl:flex">
                {NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    label={item.label}
                    active={isPathActive(location.pathname, item.to)}
                    count={item.to === '/queue' ? queueCount : undefined}
                  />
                ))}
              </nav>
            </div>
            <div className="flex shrink-0 items-center gap-1 xl:gap-2">
              <Link
                to="/search"
                className={`${touchTarget} ${COMPACT_HEADER_CONTROL_SIZE} p-2 rounded-md ${btnGhost} ${focusRing} transition-colors`}
                aria-label="Search"
                title="Search"
              >
                <Search className="w-5 h-5" aria-hidden="true" />
              </Link>
              <button
                onClick={toggleTheme}
                className={`${touchTarget} ${COMPACT_HEADER_CONTROL_SIZE} p-2 rounded-md ${btnGhost} ${focusRing} transition-colors`}
                aria-label="Toggle theme"
              >
                {theme === 'dark' ? <Sun className="w-5 h-5" aria-hidden="true" /> : <Moon className="w-5 h-5" aria-hidden="true" />}
              </button>
              {isPasswordSet && (
                <button
                  onClick={handleLogout}
                  className={`${touchTarget} ${COMPACT_HEADER_CONTROL_SIZE} p-2 rounded-md ${btnGhost} ${focusRing} transition-colors`}
                  aria-label="Log out"
                  title="Log out"
                >
                  <LogOut className="w-5 h-5" aria-hidden="true" />
                </button>
              )}
              <button
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                className={`xl:hidden ${touchTarget} ${COMPACT_HEADER_CONTROL_SIZE} p-2 rounded-md ${btnGhost} ${focusRing} transition-colors`}
                aria-label="Toggle menu"
                aria-expanded={mobileMenuOpen}
              >
                {mobileMenuOpen ? <X className="w-5 h-5" aria-hidden="true" /> : <Menu className="w-5 h-5" aria-hidden="true" />}
              </button>
            </div>
          </div>
        </div>
        {mobileMenuOpen && (
          <div className="xl:hidden max-h-[calc(100dvh-var(--status-bar-height,0px)-4rem)] overflow-y-auto overscroll-contain border-t border-border bg-card">
            <nav className="safe-x flex flex-col py-2 gap-1">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  label={item.label}
                  active={isPathActive(location.pathname, item.to)}
                  onClick={() => setMobileMenuOpen(false)}
                  count={item.to === '/queue' ? queueCount : undefined}
                />
              ))}
            </nav>
          </div>
        )}
      </header>
      <main id="main" tabIndex={-1} className="safe-x safe-x-wide max-w-7xl mx-auto py-8">
        <UpdateBanner />
        <Outlet />
      </main>
      <QuickSearch seed={searchSeed} onClose={closeSearch} />
    </div>
  );
}

export default Layout;
