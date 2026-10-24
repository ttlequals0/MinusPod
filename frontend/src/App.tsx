import { lazy, Suspense, useEffect } from 'react';
import { createBrowserRouter, Outlet, Navigate, useBlocker } from 'react-router';
import { captureDialogTrigger, ConfirmModal } from './components/Modal';
import { hasUnsavedChanges, useAnyUnsavedChanges } from './hooks/useUnsavedChanges';
import { useAppUpdate } from './utils/appUpdate';
import { RouterProvider } from 'react-router/dom';
import PullToRefresh from 'pulltorefreshjs';
import { ThemeProvider } from './context/ThemeContext';
import { AuthProvider } from './context/AuthContext';
import Layout from './components/Layout';
import ChunkLoadRecovery, { ClearChunkLoadRecoveryMarkers } from './components/ChunkLoadRecovery';
import GlobalStatusBar from './components/GlobalStatusBar';
import { SkeletonPageHeader, SkeletonRows } from './components/Skeleton';
import { btnBase, btnSecondary } from './components/buttonStyles';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const FeedDetail = lazy(() => import('./pages/FeedDetail'));
const KeyedEpisodeDetail = lazy(() => import('./pages/EpisodeDetail').then(
  (module) => ({ default: module.KeyedEpisodeDetail }),
));
const AddFeed = lazy(() => import('./pages/AddFeed'));
const Settings = lazy(() => import('./pages/Settings'));
const PatternsPage = lazy(() => import('./pages/PatternsPage'));
const SponsorsPage = lazy(() => import('./pages/SponsorsPage'));
const HistoryPage = lazy(() => import('./pages/HistoryPage'));
const StatsPage = lazy(() => import('./pages/StatsPage'));
const QueuePage = lazy(() => import('./pages/QueuePage'));
const Login = lazy(() => import('./pages/Login'));
const Search = lazy(() => import('./pages/Search'));

function RouteFallback() {
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <SkeletonPageHeader />
      <SkeletonRows count={4} />
    </div>
  );
}

function RootLayout() {
  const dirty = useAnyUnsavedChanges();
  const update = useAppUpdate();
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  return (
    <div onClickCapture={captureDialogTrigger} onKeyDownCapture={captureDialogTrigger}>
      <GlobalStatusBar />
      <div style={{ height: 'var(--status-bar-height, 0px)' }} aria-hidden="true" />
      {update && <div role="status" className="bg-card border-b border-border p-3 flex flex-wrap items-center justify-center gap-3">
        <p className="text-sm">{dirty ? 'Update ready. Save your changes before reloading.' : 'An app update is ready.'}</p>
        <button type="button" disabled={dirty} onClick={update} className={`${btnBase} ${btnSecondary}`}>Reload to update</button>
      </div>}
      <ChunkLoadRecovery>
        <Suspense fallback={<RouteFallback />}>
          <Outlet />
          <ClearChunkLoadRecoveryMarkers />
        </Suspense>
      </ChunkLoadRecovery>
      {blocker.state === 'blocked' && <ConfirmModal title="Discard unsaved changes?" confirmLabel="Discard and leave"
        onCancel={() => blocker.reset()} onConfirm={() => blocker.proceed()}>
        Your edits have not been saved. Stay on this page to save them.
      </ConfirmModal>}
    </div>
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
            { path: 'feeds/:slug/episodes/:episodeId', element: <KeyedEpisodeDetail /> },
            { path: 'add', element: <AddFeed /> },
            { path: 'search', element: <Search /> },
            { path: 'patterns', element: <PatternsPage /> },
            { path: 'sponsors', element: <SponsorsPage /> },
            { path: 'history', element: <HistoryPage /> },
            { path: 'stats', element: <StatsPage /> },
            { path: 'queue', element: <QueuePage /> },
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
  if (hasUnsavedChanges() || document.querySelector('[role="dialog"]')) return true;
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
        if (wasArmed && !hasUnsavedChanges()) window.location.reload();
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
