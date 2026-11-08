import { test, expect } from 'vitest';
import { suggestionsHandler } from './suggestions';

const rows = [1, 2, 3, 4, 5].map((id) => ({ id, status: id % 2 ? 'pending' : 'applied', kind: id > 3 ? 'trim' : 'split' }));
const load = async () => ({ status: 200, body: { suggestions: rows } });
type Body = { suggestions: typeof rows };
const ids = async (q: string) => ((await suggestionsHandler(new URL(`http://x/api/v1/patterns/cleanup/suggestions?${q}`), load))!.body as Body).suggestions.map((r) => r.id);

test('newest first by id', async () => {
  expect(await ids('')).toEqual([5, 4, 3, 2, 1]);
});
test('status and kind are exact', async () => {
  expect(await ids('status=pending')).toEqual([5, 3, 1]);
  expect(await ids('kind=trim')).toEqual([5, 4]);
});
test('before_id wins over offset', async () => {
  expect(await ids('before_id=4&offset=1&limit=2')).toEqual([3, 2]);
});
test('offset and limit slice', async () => {
  expect(await ids('offset=1&limit=2')).toEqual([4, 3]);
});
