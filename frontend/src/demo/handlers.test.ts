import { getResponse } from 'msw';
import { beforeEach, test, expect } from 'vitest';
import { handlers, isDownload, forbidden } from './handlers';
import { __setIndexForTests, __setLoaderForTests } from './fixtures';

const call = (path: string, init?: RequestInit) => getResponse(handlers, new Request('http://x/api/v1' + path, init));

beforeEach(() => {
  __setIndexForTests({ '/auth/status': 'auth.json', '/status': 'status.json', '/settings': 'settings.json', '/feeds/a/episodes/e1/notes': 'gone.json' });
  __setLoaderForTests(async (file) => (file === 'gone.json' ? { status: 410, body: { error: 'gone' } } : { status: 200, body: { file } }));
});

test('download endpoints are recognised', () => {
  expect(isDownload('/system/backup', new URLSearchParams('encrypted=true'))).toBe(true);
  expect(isDownload('/feeds/a/episodes/e/processed.mp3', new URLSearchParams('download=1'))).toBe(true);
  expect(isDownload('/feeds/a/episodes/e/runs/1/log', new URLSearchParams('format=raw'))).toBe(true);
  expect(isDownload('/feeds/a/episodes/e/runs/1/log', new URLSearchParams())).toBe(false);
  expect(isDownload('/feeds', new URLSearchParams())).toBe(false);
});

test('forbidden is a 403 with the demo body', async () => {
  const r = forbidden();
  expect(r.status).toBe(403);
  expect(await r.json()).toEqual({ error: 'Disabled in demo' });
});

test('a fixture hit returns its status and body', async () => {
  const ok = await call('/settings');
  expect(ok?.status).toBe(200);
  expect(await ok?.json()).toEqual({ file: 'settings.json' });
  const gone = await call('/feeds/a/episodes/e1/notes');
  expect(gone?.status).toBe(410);
  expect(await gone?.json()).toEqual({ error: 'gone' });
});

test('writes are rejected with the demo error', async () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const r = await call('/settings', { method, body: method === 'DELETE' ? undefined : '{}' });
    expect(r?.status).toBe(403);
    expect(await r?.text()).toContain('Disabled in demo');
  }
});

test('download and audio GETs are rejected', async () => {
  expect((await call('/system/backup'))?.status).toBe(403);
  expect((await call('/cue-templates/3/export'))?.status).toBe(403);
  expect((await call('/feeds/a/episodes/e1/processed.mp3'))?.status).toBe(403);
});

test('null-body fixture statuses return no body', async () => {
  __setLoaderForTests(async () => ({ status: 204, body: {} }));
  const r = await call('/settings');
  expect(r?.status).toBe(204);
  expect(r?.body).toBeNull();
  expect(r?.headers.get('content-type')).toBeNull();
});

test('an unknown entity is a 404', async () => {
  const r = await call('/feeds/nope/episodes/missing');
  expect(r?.status).toBe(404);
  expect(await r?.json()).toEqual({ error: 'Not found in demo' });
});

test('auth status and processing status are fixed and beat fixtures', async () => {
  expect(await (await call('/auth/status'))?.json()).toEqual({ passwordSet: false, authenticated: true });
  const status = await (await call('/status'))?.json();
  expect(status).toMatchObject({ currentJob: null, jobs: [], queueLength: 0, processingPaused: false });
  expect(status).not.toHaveProperty('file');
});
