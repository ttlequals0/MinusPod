import { createBrowserRouter, RouterProvider, Outlet, Navigate } from 'react-router-dom';
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

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
