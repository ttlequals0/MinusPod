import { test, expect } from 'vitest';
import { isDownload, forbidden } from './handlers';

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
