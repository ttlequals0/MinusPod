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
  const stats = { copied: 0, reencoded: 0, skipped: [] };
  for (const src of walk(from)) {
    const dest = join(to, relative(from, src));
    let meta;
    try {
      meta = await sharp(src).metadata();
    } catch (err) {
      // a saved error page or a format libvips lacks: that show goes without artwork
      stats.skipped.push(`${relative(from, src)}: ${err.message}`);
      continue;
    }
    mkdirSync(dirname(dest), { recursive: true });
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
    for (const line of s.skipped) console.error(`artwork: skipped ${line}`);
    console.log(`artwork: ${s.copied} files staged, ${s.reencoded} re-encoded, ${s.skipped.length} skipped`);
  } catch (err) {
    console.error(String(err.message ?? err));
    process.exit(2);
  }
}
