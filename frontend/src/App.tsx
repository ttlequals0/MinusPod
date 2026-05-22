import { useEffect } from 'react';
import { createBrowserRouter, RouterProvider, Outlet, Navigate } from 'react-router-dom';
import PullToRefresh from 'pulltorefreshjs';
import { ThemeProvider } from './context/ThemeContext';
import { AuthProvider } from './context/AuthContext';
import Layout from './components/Layout';
import GlobalStatusBar from './components/GlobalStatusBar';
import Dashboard from './pages/Dashboard';
import FeedDetail from './pages/FeedDetail';
import EpisodeDetail from './pages/EpisodeDetail';
import AddFeed from './pages/AddFeed';
import Settings from './pages/Settings';
import PatternsPage from './pages/PatternsPage';
import HistoryPage from './pages/HistoryPage';
import StatsPage from './pages/StatsPage';
import Login from './pages/Login';
import Search from './pages/Search';

function RootLayout() {
  return (
    <>
      <GlobalStatusBar />
      <Outlet />
    </>
  );
}

const router = createBrowserRouter(
  [
    {
      element: <RootLayout />,
      children: [
        { path: '/login', element: <Login /> },
        {
          path: '/',
          element: <Layout />,
          children: [
            { index: true, element: <Dashboard /> },
            { path: 'feeds/:slug', element: <FeedDetail /> },
            { path: 'feeds/:slug/episodes/:episodeId', element: <EpisodeDetail /> },
            { path: 'add', element: <AddFeed /> },
            { path: 'search', element: <Search /> },
            { path: 'patterns', element: <PatternsPage /> },
            { path: 'history', element: <HistoryPage /> },
            { path: 'stats', element: <StatsPage /> },
            { path: 'settings', element: <Settings /> },
            { path: '*', element: <Navigate to="/" replace /> },
          ],
        },
      ],
    },
  ],
  { basename: '/ui' }
);

// Pull-to-refresh gesture: pull 80px, hold 300ms, release -> window.location.reload().
// pulltorefreshjs handles the visuals; the hold timer is a side-channel touch
// listener since the library does not expose per-frame pull progress.
const PTR_DIST_THRESHOLD = 80;
const PTR_DWELL_MS = 300;

function isPtrSuppressed(): boolean {
  if (window.scrollY !== 0) return true;
  if (window.location.pathname.endsWith('/login')) return true;
  const active = document.activeElement;
  if (active instanceof HTMLElement && active.matches('input, textarea, select')) return true;
  return false;
}

function App() {
  useEffect(() => {
    let dwellTimer: ReturnType<typeof setTimeout> | null = null;
    let armed = false;
    let pullStartY: number | null = null;

    const setDwellingClass = (on: boolean) => {
      document.querySelector('.ptr--ptr')?.classList.toggle('ptr--dwelling', on);
    };

    const cancelDwell = () => {
      if (dwellTimer !== null) {
        clearTimeout(dwellTimer);
        dwellTimer = null;
      }
    };

    const onTouchStart = (e: TouchEvent) => {
      // Ignore second/third fingers so a multi-touch doesn't reset
      // the dwell reference while the primary pull is still active.
      if (pullStartY !== null) return;
      if (isPtrSuppressed()) return;
      pullStartY = e.touches[0].clientY;
    };

    const onTouchMove = (e: TouchEvent) => {
      if (pullStartY === null) return;
      const delta = e.touches[0].clientY - pullStartY;
      if (delta >= PTR_DIST_THRESHOLD) {
        if (dwellTimer === null && !armed) {
          setDwellingClass(true);
          dwellTimer = setTimeout(() => {
            armed = true;
            dwellTimer = null;
          }, PTR_DWELL_MS);
        }
      } else {
        cancelDwell();
        if (!armed) setDwellingClass(false);
      }
    };

    // onRefresh fires after touchend; keep `armed` for it to read.
    const onTouchEnd = () => {
      cancelDwell();
      pullStartY = null;
    };

    document.addEventListener('touchstart', onTouchStart, { passive: true });
    document.addEventListener('touchmove', onTouchMove, { passive: true });
    document.addEventListener('touchend', onTouchEnd, { passive: true });
    document.addEventListener('touchcancel', onTouchEnd, { passive: true });

    const ptr = PullToRefresh.init({
      mainElement: 'body',
      distThreshold: PTR_DIST_THRESHOLD,
      distMax: 120,
      distReload: PTR_DIST_THRESHOLD,
      distIgnore: 10,
      refreshTimeout: 0,
      shouldPullToRefresh: () => !isPtrSuppressed(),
      onRefresh: () => {
        const wasArmed = armed;
        cancelDwell();
        armed = false;
        pullStartY = null;
        setDwellingClass(false);
        if (wasArmed) window.location.reload();
      },
    });

    return () => {
      ptr.destroy();
      document.removeEventListener('touchstart', onTouchStart);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend', onTouchEnd);
      document.removeEventListener('touchcancel', onTouchEnd);
      cancelDwell();
    };
  }, []);

  return (
    <ThemeProvider>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
