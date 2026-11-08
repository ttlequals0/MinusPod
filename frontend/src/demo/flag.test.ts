import { expect, test } from 'vitest';
import { IS_DEMO } from './flag';

test('demo flag is off under vitest', () => {
  expect(IS_DEMO).toBe(false);
});
