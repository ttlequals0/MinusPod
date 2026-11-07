import { test, expect } from 'vitest';
import { loadAllPages } from './pagedDump';

type Body = { rows: number[]; total?: number; count?: number };
const rowsOf = (b: Body) => b.rows;
const loaderOf = (map: Record<string, Body>) => {
  const keys: string[] = [];
  const load = async (key: string) => {
    keys.push(key);
    return key in map ? { status: 200, body: map[key] } : null;
  };
  return { load, keys };
};

test('page mode assembles every recorded page', async () => {
  const { load } = loaderOf({
    '/x?page=1': { rows: [1, 2], total: 4 },
    '/x?page=2': { rows: [3, 4], total: 4 },
  });
  expect((await loadAllPages<Body, number>('/x?page=1', load, rowsOf, 2))?.rows).toEqual([1, 2, 3, 4]);
});

test('page mode throws when a later page is missing', async () => {
  const { load } = loaderOf({ '/x?page=1': { rows: [1, 2], total: 4 } });
  await expect(loadAllPages<Body, number>('/x?page=1', load, rowsOf, 2)).rejects.toThrow('incomplete fixture dump');
});

test('offset mode requests offset=(page-1)*pageSize', async () => {
  const { load, keys } = loaderOf({
    '/x?offset=0': { rows: [1, 2], count: 4 },
    '/x?offset=2': { rows: [3, 4], count: 4 },
  });
  expect((await loadAllPages<Body, number>('/x?offset=0', load, rowsOf, 2, 'offset'))?.rows).toEqual([1, 2, 3, 4]);
  expect(keys).toEqual(['/x?offset=0', '/x?offset=2']);
});

test('a short last page with the total reached does not throw', async () => {
  const { load } = loaderOf({
    '/x?page=1': { rows: [1, 2], total: 3 },
    '/x?page=2': { rows: [3], total: 3 },
  });
  expect((await loadAllPages<Body, number>('/x?page=1', load, rowsOf, 2))?.rows).toEqual([1, 2, 3]);
});

test('a dump with neither total nor count throws', async () => {
  const { load } = loaderOf({ '/x?page=1': { rows: [1] } });
  await expect(loadAllPages<Body, number>('/x?page=1', load, rowsOf)).rejects.toThrow('dump has no total');
});

test('a missing first page returns null', async () => {
  const { load } = loaderOf({});
  expect(await loadAllPages<Body, number>('/x?page=1', load, rowsOf)).toBeNull();
});
