import { test, expect } from 'vitest';
import { demoRoutes } from './App';

test('api-docs route is absent outside demo', () => {
  expect(demoRoutes()).toEqual([]);
});
