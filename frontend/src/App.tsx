import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ThemeProvider } from './context/ThemeContext';
import Layout from './components/Layout';
import GlobalStatusBar from './components/GlobalStatusBar';
import Dashboard from './pages/Dashboard';
import FeedDetail from './pages/FeedDetail';
import EpisodeDetail from './pages/EpisodeDetail';
import AddFeed from './pages/AddFeed';
import Settings from './pages/Settings';
import PatternsPage from './pages/PatternsPage';

function App() {
  return (
    <ThemeProvider>
      <BrowserRouter basename="/ui">
        <GlobalStatusBar />
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="feeds/:slug" element={<FeedDetail />} />
            <Route path="feeds/:slug/episodes/:episodeId" element={<EpisodeDetail />} />
            <Route path="add" element={<AddFeed />} />
            <Route path="patterns" element={<PatternsPage />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
