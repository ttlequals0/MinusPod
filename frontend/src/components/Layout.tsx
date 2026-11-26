import { useCallback, useState } from 'react';
import { ChartColumn, Clock, FingerprintPattern, LayoutGrid, ListOrdered, LogOut, Menu, Moon, Search, Sun, X, type LucideIcon } from 'lucide-react';
import { Outlet, Link, useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useTheme } from '../context/ThemeContext';
import { useAuth } from '../context/AuthContext';
import UpdateBanner from './UpdateBanner';
import { DemoBanner } from './DemoBanner';
import WhatsNewDialog from './WhatsNewDialog';
import { useWhatsNew } from '../hooks/useWhatsNew';
import QuickSearch, { useQuickSearchHotkey } from './QuickSearch';
import { getProcessingStatus } from '../api/status';
import { btnBase, btnPrimary, btnGhost, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { countPill, tint } from './badgeStyles';
import { uiUrl } from '../urlPrefix';
import { IS_DEMO } from '../demo/flag';

// `tab` puts a route in the phone tab bar: its icon and its position there (Queue sits before History).
type NavItem = { to: string; label: string; tab?: { Icon: LucideIcon; order: number } };
const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Feeds', tab: { Icon: LayoutGrid, order: 1 } },
  { to: '/add', label: 'Add feed' },
  { to: '/patterns', label: 'Patterns', tab: { Icon: FingerprintPattern, order: 2 } },
  { to: '/history', label: 'History', tab: { Icon: Clock, order: 4 } },
  { to: '/stats', label: 'Stats', tab: { Icon: ChartColumn, order: 5 } },
  { to: '/queue', label: 'Queue', tab: { Icon: ListOrdered, order: 3 } },
  { to: '/settings', label: 'Settings' },
  ...(IS_DEMO ? [{ to: '/api-docs', label: 'API' }] : []),
];
const TAB_ITEMS = NAV_ITEMS.flatMap(({ to, label, tab }) => (tab ? [{ to, label, ...tab }] : [])).sort((a, b) => a.order - b.order);
const COMPACT_HEADER_CONTROL_SIZE = 'h-11 w-11 xl:h-auto xl:w-auto';

interface NavLinkProps {
  to: string;
  label: string;
  active: boolean;
  onClick?: () => void;
  count?: number;
  className?: string;
}

function NavLink({ to, label, active, onClick, count, className = '' }: NavLinkProps) {
  return (
    <Link
      to={to}
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`touch-target flex min-h-11 items-center px-3 py-2 text-sm font-medium transition-colors xl:min-h-0 rounded-md ${focusRing} ${
        active
          ? 'bg-primary text-primary-foreground'
          : btnGhost
      } ${className}`}
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
  const { logout, isPasswordSet, isAuthenticated } = useAuth();
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
  const whatsNew = useWhatsNew(isAuthenticated === true);

  const handleLogout = async () => {
    await logout();
    window.location.href = uiUrl('/login');
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
      <DemoBanner />
      <header className="sticky z-30 border-b border-border bg-card" style={{ top: 'var(--status-bar-height, 0px)' }}>
        <div className="safe-x safe-x-wide max-w-7xl mx-auto">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center gap-8">
              <Link to="/" className={`touch-target flex h-11 shrink-0 items-center ${focusRing} xl:h-auto`}>
                <img
                  src={uiUrl(theme === 'dark' ? '/logo-dark.svg' : '/logo.svg')}
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
                  className={`hidden xl:inline-flex ${touchTarget} ${COMPACT_HEADER_CONTROL_SIZE} p-2 rounded-md ${btnGhost} ${focusRing} transition-colors`}
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
        {mobileMenuOpen && (() => {
          const phoneCount = NAV_ITEMS.filter((i) => !i.tab).length + (isPasswordSet ? 1 : 0);
          const tabletCount = NAV_ITEMS.length + (isPasswordSet ? 1 : 0);
          const lastSpan = `${phoneCount % 2 === 1 ? 'max-md:col-span-2' : ''} ${tabletCount % 2 === 1 ? 'md:col-span-2' : ''}`.trim();
          return (
            <div className="xl:hidden max-h-[calc(100dvh-var(--status-bar-height,0px)-4rem)] overflow-y-auto overscroll-contain border-t border-border bg-card">
              <nav aria-label="Mobile navigation" className="safe-x grid grid-cols-2 py-2.5 gap-1.5">
                {NAV_ITEMS.map((item, idx) => {
                  const isLast = !isPasswordSet && idx === NAV_ITEMS.length - 1;
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      label={item.label}
                      active={isPathActive(location.pathname, item.to)}
                      onClick={() => setMobileMenuOpen(false)}
                      count={item.to === '/queue' ? queueCount : undefined}
                      className={`${item.tab ? 'hidden md:flex' : ''} ${isLast ? lastSpan : ''}`.trim()}
                    />
                  );
                })}
                {isPasswordSet && (
                  <button
                    type="button"
                    onClick={handleLogout}
                    className={`touch-target flex min-h-11 items-center gap-2 px-3 py-2 text-sm font-medium transition-colors rounded-md ${btnGhost} ${focusRing} ${lastSpan}`}
                  >
                    <LogOut className="w-4 h-4 shrink-0" aria-hidden="true" />
                    Log out
                  </button>
                )}
              </nav>
            </div>
          );
        })()}
      </header>
      <main id="main" tabIndex={-1} className="safe-x safe-x-wide max-w-7xl mx-auto py-8 clear-bottom-nav">
        <UpdateBanner />
        <Outlet />
      </main>
      <nav
        aria-label="Primary"
        data-viewport-inset="bottom"
        className="safe-x safe-bottom fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card md:hidden"
      >
        <ul className="grid grid-cols-5">
          {TAB_ITEMS.map(({ to, label, Icon }) => {
            const active = isPathActive(location.pathname, to);
            return (
              <li key={to}>
                <Link
                  to={to}
                  aria-current={active ? 'page' : undefined}
                  className={`touch-target relative flex h-14 flex-col items-center justify-center gap-0.5 rounded-md text-xs font-medium transition-colors ${focusRing} ${
                    active ? 'text-primary' : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Icon className="h-5 w-5" aria-hidden="true" />
                  {label}
                  {to === '/queue' && queueCount > 0 && (
                    <span className={`absolute right-1/2 top-1 -mr-5 ${countPill} ${tint.primary}`}>{queueCount}</span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <QuickSearch seed={searchSeed} onClose={closeSearch} />
      {whatsNew.open && (
        <WhatsNewDialog current={whatsNew.current} sections={whatsNew.sections} onClose={whatsNew.close} />
      )}
    </div>
  );
}

export default Layout;
