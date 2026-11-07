import { afterEach, test, expect, vi } from 'vitest';
import { demoRoutes } from './App';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

test('api-docs route is absent outside demo', () => {
  expect(demoRoutes()).toEqual([]);
});

test('demo builds add the api-docs route and serve the UI at the root', async () => {
  vi.stubGlobal('__DEMO__', true);
  vi.resetModules();
  const app = await import('./App');
  const { UI_BASE } = await import('./urlPrefix');
  expect(app.demoRoutes().map((r) => r.path)).toEqual(['api-docs']);
  expect(UI_BASE).toBe('');
});
