import { describe, it, expect } from 'vitest';
import { blockPatternFor, clientName, userAgentIsBlocked } from './userAgent';

describe('clientName', () => {
  it('takes the first product token', () => {
    expect(clientName('Pocket Casts')).toBe('Pocket Casts');
    expect(clientName('AntennaPod/3.1 (Android)')).toBe('AntennaPod');
    expect(clientName('Mozilla (X11)')).toBe('Mozilla');
    expect(clientName('')).toBe('Unknown');
  });
});

describe('blockPatternFor', () => {
  it('anchors the full string when under 40 characters', () => {
    expect(blockPatternFor('Pocket Casts')).toBe('^Pocket Casts');
    expect(blockPatternFor('AntennaPod/3.1 (Android)')).toBe('^AntennaPod/3.1 (Android)');
  });
  it('anchors the first product token for longer agents', () => {
    expect(blockPatternFor('Overcast/3.0 (+http://overcast.fm/; iOS)')).toBe('^Overcast');
  });
});

describe('userAgentIsBlocked', () => {
  it('matches a ^ pattern at the start only, case-insensitively', () => {
    expect(userAgentIsBlocked('Pocket Casts/7', ['^pocket'])).toBe(true);
    expect(userAgentIsBlocked('Not Pocket Casts', ['^pocket'])).toBe(false);
  });
  it('matches other patterns anywhere', () => {
    expect(userAgentIsBlocked('Not Pocket Casts', ['CASTS'])).toBe(true);
  });
  it('is false for no patterns', () => {
    expect(userAgentIsBlocked('Pocket Casts', [])).toBe(false);
  });
});
