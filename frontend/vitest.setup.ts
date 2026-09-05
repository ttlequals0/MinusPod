import { Storage } from 'happy-dom';

// Node 25+ defines its own localStorage and sessionStorage getters that yield
// undefined without --localstorage-file. Vitest skips window keys that already
// exist on the global, so the test DOM's Storage never lands. Install one.
for (const key of ['localStorage', 'sessionStorage'] as const) {
  if (!globalThis[key]) {
    Object.defineProperty(globalThis, key, {
      value: new Storage(), configurable: true, writable: true,
    });
  }
}
