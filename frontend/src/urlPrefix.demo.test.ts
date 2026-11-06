import { test, expect } from 'vitest';
import { UI_BASE, API_BASE, uiUrl } from './urlPrefix';

test('non-demo base is /ui', () => {
  expect(UI_BASE).toBe('/ui');
  expect(API_BASE).toBe('/api/v1');
  expect(uiUrl('/login')).toBe('/ui/login');
});
