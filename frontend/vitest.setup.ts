import { Storage } from 'happy-dom';

window.happyDOM.settings.fetch.interceptor = {
  beforeAsyncRequest: async ({ request }) => {
    throw new Error(`Unexpected test resource request: ${request.url}`);
  },
};

// Node 25+ defines its own localStorage and sessionStorage getters that yield
// undefined without --localstorage-file. Vitest skips window keys that already
// exist on the global, so the test DOM's Storage never lands. Install one.
for (const key of ['localStorage', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, key, {
    value: new Storage(), configurable: true, writable: true,
  });
}
