import { describe, it, expect } from 'vitest';
import { blockPatternFor, clientName } from './userAgent';

describe('clientName', () => {
  it('takes the first product token', () => {
    expect(clientName('Pocket Casts')).toBe('Pocket Casts');
    expect(clientName('AntennaPod/3.1 (Android)')).toBe('AntennaPod');
    expect(clientName('Mozilla (X11)')).toBe('Mozilla');
    expect(clientName('')).toBe('Unknown');
  });
});

describe('blockPatternFor', () => {
  it('anchors the first product token', () => {
    expect(blockPatternFor('Pocket Casts')).toBe('^Pocket Casts');
    expect(blockPatternFor('AntennaPod/3.1 (Android)')).toBe('^AntennaPod');
    expect(blockPatternFor('Overcast/3.0 (+http://overcast.fm/; iOS)')).toBe('^Overcast');
  });

  it('returns null when the product token is empty', () => {
    expect(blockPatternFor('')).toBeNull();
    expect(blockPatternFor('/1.0 (x)')).toBeNull();
  });
});
