import { copyFileSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join, resolve, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { writeAtomic } from './lib/store.mjs';
import { argValue } from './lib/args.mjs';

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
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
  const from = argValue(process.argv, '--from');
  const to = argValue(process.argv, '--to');
  if (!from || !to) {
    console.error('usage: stage-artwork.mjs --from <artwork dir> --to <frontend/demo/artwork>');
    process.exit(2);
  }
  const s = await stageArtwork(resolve(from), resolve(to));
  console.log(`artwork: ${s.copied} files staged, ${s.reencoded} re-encoded`);
}
