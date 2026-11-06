import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repo = resolve(frontend, '..');
const out = resolve(frontend, 'demo/public');

function fail(msg) {
  console.error(`demo-assemble: ${msg}`);
  process.exit(1);
}

function copyOptional(src, dest) {
  if (!existsSync(src)) {
    console.warn(`demo-assemble: missing ${src}, skipping`);
    return;
  }
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(src, dest, { recursive: true });
}

const worker = resolve(frontend, 'node_modules/msw/lib/mockServiceWorker.js');
const openapi = resolve(repo, 'openapi.yaml');
if (!existsSync(worker)) fail(`missing ${worker}, run npm install`);
if (!existsSync(openapi)) fail(`missing ${openapi}`);

const versionMatch = readFileSync(resolve(repo, 'version.py'), 'utf8').match(/__version__\s*=\s*"([^"]+)"/);
if (!versionMatch) fail('could not read __version__ from version.py');

rmSync(out, { recursive: true, force: true });
mkdirSync(resolve(out, 'demo'), { recursive: true });

cpSync(resolve(frontend, 'public'), out, { recursive: true });
cpSync(resolve(frontend, 'demo/static'), out, { recursive: true });
cpSync(worker, resolve(out, 'mockServiceWorker.js'));

const spec = readFileSync(openapi, 'utf8').replace(/^(\s*version:\s*).*$/m, `$1${versionMatch[1]}`);
writeFileSync(resolve(out, 'demo/openapi.yaml'), spec);

copyOptional(resolve(frontend, 'demo/fixtures'), resolve(out, 'demo/fixtures'));
copyOptional(resolve(frontend, 'demo/artwork'), resolve(out, 'demo/artwork'));
copyOptional(resolve(frontend, 'demo/search-index.json'), resolve(out, 'demo/search-index.json'));
