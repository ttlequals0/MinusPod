import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Workbox } from 'workbox-window';
import App from './App';
import './index.css';
import { offerAppUpdate } from './utils/appUpdate';
import { UI_BASE } from './urlPrefix';

// Let the user apply updates after saving drafts.
if ('serviceWorker' in navigator) {
  const wb = new Workbox(`${UI_BASE}/sw.js`, { scope: `${UI_BASE}/` });
  wb.addEventListener('waiting', () => {
    offerAppUpdate(() => {
      const reload = () => {
        wb.removeEventListener('controlling', reload);
        window.location.reload();
      };
      wb.addEventListener('controlling', reload);
      wb.messageSkipWaiting();
    });
  });
  wb.register().catch(error => { console.error('Service worker registration failed', error); });
}

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
