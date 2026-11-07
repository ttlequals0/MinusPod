import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Workbox } from 'workbox-window';
import App from './App';
import './index.css';
import { offerAppUpdate } from './utils/appUpdate';
import { UI_BASE } from './urlPrefix';
import { DEMO_BOOT_ERROR } from './demo/copy';

async function boot() {
  if (__DEMO__) {
    const { startDemo } = await import('./demo/start');
    await startDemo();
  }

  // Let the user apply updates after saving drafts.
  if (!__DEMO__ && 'serviceWorker' in navigator) {
    const wb = new Workbox(`${UI_BASE}/sw.js`, { scope: `${UI_BASE}/` });
    wb.addEventListener('waiting', () => {
      offerAppUpdate(() => {
        wb.addEventListener('controlling', () => window.location.reload());
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
}

boot().catch((err: unknown) => {
  console.error(err);
  const root = document.getElementById('root');
  if (__DEMO__ && root) root.textContent = DEMO_BOOT_ERROR;
});
