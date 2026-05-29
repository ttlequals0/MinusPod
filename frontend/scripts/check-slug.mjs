// Run with: node --experimental-strip-types frontend/scripts/check-slug.mjs
// Asserts the TS slugify port matches the canonical Python outputs at
// tests/unit/test_community_tags.py. Treat as a sanity gate at PR
// review time, not a CI step.
import { slugify, expectedFilename } from '../src/api/communitySlug.ts';

const cases = [
  ['Shopify', 'shopify'],
  ['TD Bank', 'td-bank'],
  ['Capital One', 'capital-one'],
  ['Hims.com', 'hims-com'],
  ['badcholesterol.com', 'badcholesterol-com'],
  ['  Spaces   Everywhere  ', 'spaces-everywhere'],
  ['', 'sponsor'],
  ['!!!', 'sponsor'],
];
let failed = 0;
for (const [input, expected] of cases) {
  const got = slugify(input);
  if (got !== expected) {
    console.error(`slugify(${JSON.stringify(input)}) = ${JSON.stringify(got)}, expected ${JSON.stringify(expected)}`);
    failed += 1;
  }
}
const fn = expectedFilename('Shopify', '07df78ed-9b7f-4600-a9b7-1aee45b5bfc7');
if (fn !== 'shopify-07df78ed.json') {
  console.error(`expectedFilename returned ${fn}`);
  failed += 1;
}
const empty = expectedFilename('Shopify', '');
if (empty !== null) {
  console.error(`expectedFilename empty-cid returned ${empty}, expected null`);
  failed += 1;
}
if (failed > 0) {
  console.error(`${failed} case(s) failed`);
  process.exit(1);
}
console.log(`OK: ${cases.length + 2} cases passed`);
