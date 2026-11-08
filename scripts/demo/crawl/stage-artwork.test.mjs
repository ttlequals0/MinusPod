import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

test('a non-empty --to is refused unless --clean, which removes the old files', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'art-'));
  const from = join(dir, 'from');
  const to = join(dir, 'to');
  mkdirSync(join(from, 'feeds'), { recursive: true });
  mkdirSync(to);
  writeFileSync(join(from, 'feeds', 'a.jpg'), await sharp({ create: { width: 4, height: 4, channels: 3, background: 'red' } }).jpeg().toBuffer());
  writeFileSync(join(to, 'stale.jpg'), 'x');
  const script = fileURLToPath(new URL('./stage-artwork.mjs', import.meta.url));
  const refused = spawnSync(process.execPath, [script, '--from', from, '--to', to]);
  assert.notEqual(refused.status, 0);
  assert.match(String(refused.stderr), /--clean/);
  assert.deepEqual(readdirSync(to), ['stale.jpg']);
  const cleaned = spawnSync(process.execPath, [script, '--from', from, '--to', to, '--clean']);
  assert.equal(cleaned.status, 0, String(cleaned.stderr));
  assert.equal(existsSync(join(to, 'stale.jpg')), false);
  assert.ok(existsSync(join(to, 'feeds', 'a.jpg')));
});
