import { copyFileSync, mkdirSync, readdirSync } from 'node:fs';
import { join, resolve, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { writeAtomic, prepareOutDir } from './lib/store.mjs';
import { argValue, checkFlags } from './lib/args.mjs';

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue; // macOS ._* and .DS_Store
    const p = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(p);
    else yield p;
  }
}

export async function stageArtwork(from, to) {
  const stats = { copied: 0, reencoded: 0 };
  for (const src of walk(from)) {
    const dest = join(to, relative(from, src));
    mkdirSync(dirname(dest), { recursive: true });
    const meta = await sharp(src).metadata();
    if (meta.format === 'jpeg') {
      copyFileSync(src, dest);
    } else {
      writeAtomic(dest, await sharp(src).jpeg({ quality: 85 }).toBuffer());
      stats.reencoded++;
    }
    stats.copied++;
  }
  return stats;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    checkFlags(process.argv.slice(2), ['--from', '--to', '--clean']);
    const from = argValue(process.argv, '--from');
    const to = argValue(process.argv, '--to');
    if (!from || !to) throw new Error('usage: stage-artwork.mjs --from <artwork dir> --to <frontend/demo/artwork> [--clean]');
    prepareOutDir(resolve(to), process.argv.includes('--clean'));
    const s = await stageArtwork(resolve(from), resolve(to));
    console.log(`artwork: ${s.copied} files staged, ${s.reencoded} re-encoded`);
  } catch (err) {
    console.error(String(err.message ?? err));
    process.exit(2);
  }
}
