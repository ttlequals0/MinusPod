import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';
import { loadFixtureIndex } from './fixtures';

export async function startDemo(): Promise<void> {
  const worker = setupWorker(...handlers);
  await Promise.all([
    loadFixtureIndex(),
    worker.start({
      serviceWorker: { url: '/mockServiceWorker.js' },
      onUnhandledFrame({ frame, defaults }) {
        if (frame.protocol !== 'http') return;
        const { request } = frame.data as { request: Request };
        if (new URL(request.url).pathname.startsWith('/api/')) defaults.warn();
      },
    }),
  ]);
}
