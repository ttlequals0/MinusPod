import { test, expect } from 'vitest';
import { peaksHandler } from './peaks';

const whole = { status: 200, body: { episodeId: 'e1', start: 0, end: 2.5, resolutionMs: 250, peaks: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] } };
const load = async (key: string) => (key === '/feeds/a/episodes/e1/peaks' ? whole : null);
const get = (q: string, path = '/feeds/a/episodes/e1/peaks') => peaksHandler(new URL(`http://x/api/v1${path}?${q}`), load);

test('a window is sliced from the recording at its own resolution', async () => {
  expect(await get('start=0.6&end=1.3&resolution_ms=100')).toEqual({
    status: 200,
    body: { episodeId: 'e1', start: 0.5, end: 1.5, resolutionMs: 250, peaks: [2, 3, 4, 5] },
  });
});
test('an open or oversized end runs to the end of the recording', async () => {
  expect(((await get('start=2'))!.body as { peaks: number[] }).peaks).toEqual([8, 9]);
  expect(((await get('start=0&end=99'))!.body as { peaks: number[] }).peaks).toHaveLength(10);
});
test('an end at or before the start is an empty window', async () => {
  expect(((await get('start=1&end=0.5'))!.body as { peaks: number[] }).peaks).toEqual([]);
  expect(((await get('start=1&end=1'))!.body as { peaks: number[] }).peaks).toEqual([]);
});
test('an episode without a recording is a 404 with demo copy', async () => {
  const res = await get('start=0&end=1', '/feeds/a/episodes/none/peaks');
  expect(res!.status).toBe(404);
  expect((res!.body as { error: string }).error).toMatch(/demo/);
});
