import { test, expect } from 'vitest';
import { patternsHandler } from './patterns';

const rows = [
  { id: 1, scope: 'global', podcast_id: null, network_id: null, is_active: true, source: 'local' },
  { id: 2, scope: 'podcast', podcast_id: 'p1', network_id: null, is_active: false, source: 'community' },
  { id: 3, scope: 'network', podcast_id: null, network_id: 'n1', is_active: true, source: 'community' },
];
const load = async () => ({ status: 200, body: { patterns: rows } });
type Body = { patterns: typeof rows };
const ids = async (q: string) => ((await patternsHandler(new URL(`http://x/api/v1/patterns?${q}`), load))!.body as Body).patterns.map((r) => r.id);

test('scope all and no params return everything', async () => {
  expect(await ids('')).toEqual([1, 2, 3]);
  expect(await ids('scope=all')).toEqual([1, 2, 3]);
});
test('scope, podcast_id and network_id are exact', async () => {
  expect(await ids('scope=network')).toEqual([3]);
  expect(await ids('podcast_id=p1')).toEqual([2]);
  expect(await ids('network_id=n1')).toEqual([3]);
});
test('active and active_only true keep active rows', async () => {
  expect(await ids('active=true')).toEqual([1, 3]);
  expect(await ids('active_only=true')).toEqual([1, 3]);
});
test('invalid source is ignored', async () => {
  expect(await ids('source=community')).toEqual([2, 3]);
  expect(await ids('source=nope')).toEqual([1, 2, 3]);
});
