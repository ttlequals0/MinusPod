import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { FINGERPRINT_FALLBACK_LIMITS } from './AudioFingerprintingSection';

const spec = readFileSync(resolve(__dirname, '../../../../openapi.yaml'), 'utf8').split('\n');

// Every block of lines nested under `key:`, which the spec repeats for the request and the response.
function blocks(key: string): string[][] {
  const found: string[][] = [];
  spec.forEach((line, i) => {
    const indent = line.match(/^( *)/)![1].length;
    if (line.trim() !== `${key}:`) return;
    const body: string[] = [];
    for (let j = i + 1; j < spec.length; j++) {
      const next = spec[j];
      if (next.trim() !== '' && next.match(/^( *)/)![1].length <= indent) break;
      body.push(next.trim());
    }
    found.push(body);
  });
  return found;
}

const numberOf = (body: string[], field: string) => {
  const line = body.find((l) => l.startsWith(`${field}: `));
  return line === undefined ? undefined : Number(line.slice(field.length + 2));
};

describe('audio fingerprint limits against openapi.yaml', () => {
  const settings = {
    matchStrictness: 'fingerprintMatchStrictness',
    minCoverage: 'fingerprintMinCoverage',
    gapToleranceS: 'fingerprintGapToleranceS',
    minAdLengthS: 'fingerprintMinAdLengthS',
  } as const;

  it.each(Object.entries(settings))('%s matches the schema minimum, maximum and default', (name, key) => {
    const found = blocks(key);
    expect(found.length).toBeGreaterThanOrEqual(2);
    for (const body of found) {
      const limits = FINGERPRINT_FALLBACK_LIMITS[name as keyof typeof settings];
      expect(numberOf(body, 'minimum')).toBe(limits.min);
      expect(numberOf(body, 'maximum')).toBe(limits.max);
      expect(numberOf(body, 'default')).toBe(limits.default);
    }
  });

  it('pins the fallback steps to what the server serves', () => {
    expect(Object.fromEntries(Object.entries(FINGERPRINT_FALLBACK_LIMITS).map(([k, v]) => [k, v.step]))).toEqual({
      matchStrictness: 0.05, minCoverage: 0.05, gapToleranceS: 0.5, minAdLengthS: 1,
    });
  });

  it('documents the enable switch as on by default', () => {
    const found = blocks('fingerprintEnabled');
    expect(found.length).toBeGreaterThanOrEqual(2);
    for (const body of found) expect(body).toContain('default: true');
  });
});
