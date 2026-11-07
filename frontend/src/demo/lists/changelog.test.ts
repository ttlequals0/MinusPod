import { test, expect } from 'vitest';
import { changelogHandler } from './changelog';

const sections = ['2.10.0', '2.9.1', '2.9.0', '1.0.0'].map((version) => ({ version, date: 'd', body: 'b' }));
type Body = { current: string; sections: typeof sections };
const load = async () => ({ status: 200, body: { current: '2.10.0', sections } });
const get = async (q: string) => changelogHandler(new URL(`http://x/api/v1/changelog?${q}`), load);
const versions = async (q: string) => ((await get(q))!.body as Body).sections.map((s) => s.version);

test('since excludes the equal version and compares numerically', async () => {
  expect(await versions('since=2.9.0')).toEqual(['2.10.0', '2.9.1']);
});
test('limit defaults to 10 and caps at 50', async () => {
  expect(await versions('')).toHaveLength(4);
  expect(await versions('limit=2')).toEqual(['2.10.0', '2.9.1']);
});
test('bad since and bad limit are 400s', async () => {
  expect((await get('since=abc'))!.status).toBe(400);
  expect((await get('limit=0'))!.status).toBe(400);
});
test('current is preserved', async () => {
  expect(((await get('limit=1'))!.body as Body).current).toBe('2.10.0');
});
