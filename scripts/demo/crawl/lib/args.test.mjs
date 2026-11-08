import test from 'node:test';
import assert from 'node:assert/strict';
import { argValue, checkFlags } from './args.mjs';

test('checkFlags accepts known flags and names unknown ones', () => {
  checkFlags(['--base', 'http://h', '--out', 'd'], ['--base', '--out']);
  assert.throws(() => checkFlags(['--out', 'd', '--overrides', 'o'], ['--base', '--out']), /unknown option --overrides/);
  assert.equal(argValue(['--out', 'd'], '--out'), 'd');
});
