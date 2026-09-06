import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { registerSW } from 'virtual:pwa-register';
import App from './App';
import './index.css';

// A new service worker means a deploy; reload to leave the precached old bundle, but only once the tab is
// hidden so an edit in progress is not thrown away mid-keystroke.
const applyUpdate = registerSW({
  immediate: true,
  onNeedRefresh() {
    if (document.visibilityState === 'hidden') { void applyUpdate(true); return; }
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') void applyUpdate(true);
    }, { once: true });
  },
  onRegisterError(error) { console.error('Service worker registration failed', error); },
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
