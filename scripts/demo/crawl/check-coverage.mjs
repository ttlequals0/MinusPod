import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readIndex } from './lib/store.mjs';
import { argValue } from './lib/args.mjs';

export const DEFAULT_ALLOW = [
  '/system/backup', '/system/config-export', '/system/config-backup', '/system/diagnostic-export',
  '/history/export', '/feeds/export-opml', '/patterns/export', '/cue-templates/*/export',
  '/cue-templates/*/audio', '/feeds/*/episodes/*/runs/*/log', '/youtube/search',
  '/settings/youtube/sb-user-id', '/settings/provider-budget/rate/*', '/feeds/*/import/status',
  // the demo serves these from its own handlers
  '/podcast-search', '/feeds/*/episodes/*/peaks',
  '/feeds/*/episodes/*/cue-candidates', '/feeds/*/episodes/*/transcript-span',
  '/feeds/*/episodes/*/split-candidates',
  '/patterns/*/audio', '/patterns/*/audio/matches',
];

const CALL = /\bapi(?:File)?Request\b\s*(?:<[^()`]*>)?\s*\(\s*/g;
const QUERY_EXPR = /qs|query|suffix|params|refresh|\?/i;

// Reads a quoted literal at i; returns [text with ${...} placeholders, end index]. Nested templates are balanced.
function readLiteral(src, i) {
  const q = src[i];
  let out = '';
  for (let j = i + 1; j < src.length; j++) {
    const c = src[j];
    if (c === '\\') { j++; continue; }
    if (c === q) return [out, j + 1];
    if (q === '`' && c === '$' && src[j + 1] === '{') {
      let depth = 1;
      let k = j + 2;
      for (; k < src.length && depth; k++) {
        if (src[k] === '`') k = readLiteral(src, k)[1] - 1;
        else if (src[k] === '{') depth++;
        else if (src[k] === '}') depth--;
      }
      out += `\u0000${src.slice(j + 2, k - 1)}\u0001`;
      j = k - 1;
      continue;
    }
    out += c;
  }
  return null;
}

// Paren-balanced text of the rest of the call, to see its options object.
function restOfCall(src, i) {
  let depth = 1;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '(') depth++;
    else if (src[j] === ')' && --depth === 0) return src.slice(i, j);
  }
  return src.slice(i);
}

// Templates of apiRequest/apiFileRequest calls that are GETs (no non-GET `method`).
export function extractGetTemplates(source) {
  const out = new Set();
  for (const m of source.matchAll(CALL)) {
    const start = m.index + m[0].length;
    if (!'`\'"'.includes(source[start])) continue;
    const lit = readLiteral(source, start);
    if (!lit) continue;
    if (/\bmethod\s*:\s*['"`](?!GET)/i.test(restOfCall(source, lit[1]))) continue;
    let t = lit[0].replace(/\u0000([^\u0001]*)\u0001$/, (_, e) => (QUERY_EXPR.test(e) ? '' : '*'));
    t = t.replace(/\u0000[^\u0001]*\u0001/g, '*').split('?')[0];
    if (t.startsWith('/')) out.add(t.length > 1 ? t.replace(/\/$/, '') : t);
  }
  return [...out].sort();
}

const toRegex = (tpl) => new RegExp(`^${tpl.split('*').map((s) => s.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+')}$`);

export function uncovered(templates, keys, allow = DEFAULT_ALLOW) {
  const paths = keys.map((k) => k.split('?')[0]);
  const allowRe = allow.map(toRegex);
  return templates.filter((t) => {
    if (allow.includes(t) || allowRe.some((re) => re.test(t))) return false;
    const re = toRegex(t);
    return !paths.some((p) => re.test(p));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const apiDir = argValue(process.argv, '--api-dir');
  const fixtures = argValue(process.argv, '--fixtures');
  const allowFile = argValue(process.argv, '--allow');
  if (!apiDir || !fixtures) {
    console.error('usage: check-coverage.mjs --api-dir <frontend/src/api> --fixtures <dir> [--allow <json>]');
    process.exit(2);
  }
  const allow = allowFile ? JSON.parse(readFileSync(resolve(allowFile), 'utf8')) : DEFAULT_ALLOW;
  const files = readdirSync(resolve(apiDir)).filter((f) => /\.ts$/.test(f) && !/\.test\.ts$/.test(f));
  const templates = [...new Set(files.flatMap((f) => extractGetTemplates(readFileSync(join(resolve(apiDir), f), 'utf8'))))].sort();
  const miss = uncovered(templates, Object.keys(readIndex(resolve(fixtures))), allow);
  for (const m of miss) console.log(`uncovered: ${m}`);
  console.log(`${templates.length} GET templates, ${miss.length} uncovered`);
  process.exit(miss.length ? 1 : 0);
}
