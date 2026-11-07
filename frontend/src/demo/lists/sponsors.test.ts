import { test, expect } from 'vitest';
import { sponsorsHandler } from './sponsors';

const rows = [{ id: 1, is_active: true }, { id: 2, is_active: false }, { id: 3 }];
const load = async () => ({ status: 200, body: { sponsors: rows } });
type Body = { sponsors: typeof rows };
const ids = async (q: string) => ((await sponsorsHandler(new URL(`http://x/api/v1/sponsors?${q}`), load))!.body as Body).sponsors.map((r) => r.id);

test('inactive sponsors are dropped by default', async () => {
  expect(await ids('')).toEqual([1, 3]);
});
test('include_inactive=true keeps them', async () => {
  expect(await ids('include_inactive=true')).toEqual([1, 2, 3]);
});
test('missing dump returns null', async () => {
  expect(await sponsorsHandler(new URL('http://x/api/v1/sponsors'), async () => null)).toBeNull();
});
