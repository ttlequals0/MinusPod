import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixtureKey, fileFor } from './lib/keys.mjs';
import { scrubFeedKeys, writeFixture, mergeIndex, prepareOutDir } from './lib/store.mjs';
import { argValue, checkFlags } from './lib/args.mjs';

const API_PATH = '/api/v1/';

function decodeBody(content) {
  const text = content?.text ?? '';
  return content?.encoding === 'base64' ? Buffer.from(text, 'base64').toString('utf8') : text;
}

export function entriesToFixtures(har) {
  const out = new Map();
  for (const { request, response } of har.log.entries) {
    if (request.method !== 'GET') continue;
    const url = new URL(request.url);
    if (!url.pathname.startsWith(API_PATH)) continue;
    if (!/json/i.test(response.content?.mimeType ?? '')) continue;
    const { status } = response;
    let fixture = { status };
    if (status !== 204 && status !== 304) {
      try {
        fixture = { status, body: scrubFeedKeys(JSON.parse(decodeBody(response.content))) };
      } catch { /* unparseable body: keep status only */ }
    }
    out.set(fixtureKey(url), fixture);
  }
  return out;
}

function main(argv) {
  checkFlags(argv, ['--har', '--out', '--clean']);
  const harPath = argValue(argv, '--har');
  const out = argValue(argv, '--out');
  if (!harPath || !out) throw new Error('usage: --har <file> --out <dir> [--clean]');
  const fixtures = entriesToFixtures(JSON.parse(readFileSync(harPath, 'utf8')));
  prepareOutDir(out, argv.includes('--clean'));
  const index = {};
  for (const [key, fixture] of fixtures) index[key] = writeFixture(out, key, fixture);
  mergeIndex(out, index);
  console.log(`wrote ${fixtures.size} fixtures to ${out}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2));
