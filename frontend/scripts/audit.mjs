// Run from frontend/: node scripts/audit.mjs
// The CI audit step. Fails on any advisory of high or critical severity, as
// `npm audit --audit-level=high` does, except the advisories in EXCEPTIONS.
// npm audit has no flag for that. Pass a saved `npm audit --json` report as the
// first argument to check that instead of running npm.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

// An exception is for an advisory with no patched release to move to. Remove
// the entry once one exists, and clear the advisory with a bump or an override.
const EXCEPTIONS = {
  // ReDoS on crafted extglobs in picomatch <= 4.0.7, the latest release as of
  // 2026-11-18. It reaches the tree only through build, lint and test tooling,
  // which matches globs written in this repository.
  'GHSA-fc5q-39xc-vjpf': 'picomatch has no patched release',
};
const BLOCKING = new Set(['high', 'critical']);

function readReport(file) {
  if (file) return readFileSync(file, 'utf8');
  try {
    return execFileSync('npm', ['audit', '--json'], {
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
    });
  } catch (err) {
    // npm audit exits non-zero when it reports anything; the report is on stdout.
    if (err.stdout) return err.stdout;
    throw err;
  }
}

const report = JSON.parse(readReport(process.argv[2]));
if (!report.vulnerabilities) {
  console.error('npm audit returned no report:', JSON.stringify(report.error ?? report).slice(0, 500));
  process.exit(1);
}

// A `via` entry is an advisory object, or the name of a dependency that carries one.
const advisories = new Map();
for (const entry of Object.values(report.vulnerabilities)) {
  for (const via of entry.via) {
    if (typeof via === 'object' && BLOCKING.has(via.severity)) {
      advisories.set(via.url.split('/').pop(), via);
    }
  }
}

const blocking = [...advisories].filter(([id]) => !(id in EXCEPTIONS));
for (const [id, advisory] of advisories) {
  if (id in EXCEPTIONS) console.log(`Excepted ${id} (${advisory.name}): ${EXCEPTIONS[id]}`);
}
for (const id of Object.keys(EXCEPTIONS)) {
  if (!advisories.has(id)) {
    console.warn(`${id} is no longer reported. Remove it from EXCEPTIONS in scripts/audit.mjs.`);
  }
}
if (blocking.length > 0) {
  for (const [id, advisory] of blocking) {
    console.error(`${advisory.severity}: ${advisory.name} ${advisory.range} - ${advisory.title} (${id})`);
  }
  console.error(`${blocking.length} advisory(ies) of high or critical severity. Run npm audit for the dependency paths.`);
  process.exit(1);
}
console.log('No high or critical advisories outside the exceptions.');
