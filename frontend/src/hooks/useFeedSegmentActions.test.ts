import { describe, expect, it } from 'vitest';
import { resolveSegmentActions } from './useFeedSegmentActions';

describe('resolveSegmentActions', () => {
  it('defaults every category to remove', () => {
    const resolved = resolveSegmentActions(null, undefined);
    expect(Object.keys(resolved)).toHaveLength(7);
    expect(new Set(Object.values(resolved))).toEqual(new Set(['remove']));
  });

  it('takes the global setting over the default and the feed override over both', () => {
    const resolved = resolveSegmentActions(
      { intro: 'remove', recap: 'mark' },
      { intro: 'keep', outro: 'keep' },
    );
    expect(resolved.intro).toBe('remove');
    expect(resolved.recap).toBe('mark');
    expect(resolved.outro).toBe('keep');
    expect(resolved.sponsor).toBe('remove');
  });
});
