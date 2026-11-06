/* eslint-disable @typescript-eslint/no-explicit-any */
import { test, expect } from 'vitest';
import { processingHandler } from './processing';

const rows = [
  { episodeId: 'run', stage: 'transcribing', queueTotal: 3 },
  ...[1, 2, 3].map((n) => ({ episodeId: `q${n}`, stage: 'queued', queuePosition: n, queueTotal: 3 })),
];
const load = async () => ({ status: 200, body: rows });
const get = async (q: string) => (await processingHandler(new URL(`http://x/api/v1/episodes/processing?${q}`), load))!.body as any[];

test('active rows always lead and the queue is sliced', async () => {
  const b = await get('offset=1&limit=1');
  expect(b.map((e) => e.episodeId)).toEqual(['run', 'q2']);
});
test('queuePosition is offset aware', async () => {
  const b = await get('offset=1&limit=2');
  expect(b.filter((e) => e.stage === 'queued').map((e) => e.queuePosition)).toEqual([2, 3]);
});
test('default limit returns the whole queue', async () => {
  expect(await get('')).toHaveLength(4);
});
