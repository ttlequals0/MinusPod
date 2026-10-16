import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { registerSW } from 'virtual:pwa-register';
import App from './App';
import './index.css';
import { offerAppUpdate } from './utils/appUpdate';

// Let the user apply updates after saving drafts.
const applyUpdate = registerSW({
  immediate: true,
  onNeedRefresh() {
    offerAppUpdate(() => { void applyUpdate(true); });
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
