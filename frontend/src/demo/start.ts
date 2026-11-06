import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';
import { loadFixtureIndex } from './fixtures';

export async function startDemo(): Promise<void> {
  await loadFixtureIndex();
  const worker = setupWorker(...handlers);
  await worker.start({
    serviceWorker: { url: '/mockServiceWorker.js' },
    onUnhandledFrame({ frame, defaults }) {
      if (frame.protocol !== 'http') return;
      const { request } = frame.data as { request: Request };
      if (new URL(request.url).pathname.startsWith('/api/')) defaults.warn();
    },
  });
}
