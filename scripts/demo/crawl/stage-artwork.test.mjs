import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const script = fileURLToPath(new URL('./stage-artwork.mjs', import.meta.url));

async function artTree() {
  const dir = mkdtempSync(join(tmpdir(), 'art-'));
  const from = join(dir, 'from');
  const to = join(dir, 'to');
  mkdirSync(join(from, 'feeds'), { recursive: true });
  mkdirSync(to);
  writeFileSync(join(from, 'feeds', 'a.jpg'), await sharp({ create: { width: 4, height: 4, channels: 3, background: 'red' } }).jpeg().toBuffer());
  return { from, to };
}

const stage = (from, to, ...extra) => spawnSync(process.execPath, [script, '--from', from, '--to', to, ...extra]);

test('a non-empty --to is refused unless --clean, which removes the old files', async () => {
  const { from, to } = await artTree();
  writeFileSync(join(to, 'stale.jpg'), 'x');
  const refused = stage(from, to);
  assert.notEqual(refused.status, 0);
  assert.match(String(refused.stderr), /--clean/);
  assert.deepEqual(readdirSync(to), ['stale.jpg']);
  const cleaned = stage(from, to, '--clean');
  assert.equal(cleaned.status, 0, String(cleaned.stderr));
  assert.equal(existsSync(join(to, 'stale.jpg')), false);
  assert.ok(existsSync(join(to, 'feeds', 'a.jpg')));
});

test('dotfiles such as macOS ._* and .DS_Store are skipped', async () => {
  const { from, to } = await artTree();
  writeFileSync(join(from, 'feeds', '._a.jpg'), 'not an image');
  writeFileSync(join(from, '._feeds'), 'not an image');
  writeFileSync(join(from, '.DS_Store'), 'not an image');
  const r = stage(from, to);
  assert.equal(r.status, 0, String(r.stderr));
  assert.deepEqual(readdirSync(join(to, 'feeds')), ['a.jpg']);
  assert.deepEqual(readdirSync(to), ['feeds']);
});

test('a file sharp cannot read is skipped and named, the rest is staged', async () => {
  const { from, to } = await artTree();
  writeFileSync(join(from, 'feeds', 'b.jpg'), '<html>not found</html>');
  const r = stage(from, to);
  assert.equal(r.status, 0, String(r.stderr));
  assert.match(String(r.stderr), /artwork: skipped feeds\/b\.jpg: /);
  assert.match(String(r.stdout), /1 files staged, 0 re-encoded, 1 skipped/);
  assert.deepEqual(readdirSync(join(to, 'feeds')), ['a.jpg']);
});
