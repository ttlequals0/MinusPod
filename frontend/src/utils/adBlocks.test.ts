import { describe, expect, it } from 'vitest';
import { groupAdBlocks } from './adBlocks';

const m = (start: number, end: number) => ({ start, end });

describe('groupAdBlocks', () => {
  it('returns nothing for no markers', () => {
    expect(groupAdBlocks([])).toEqual([]);
  });

  it('keeps markers apart when gaps exceed 5 s', () => {
    const blocks = groupAdBlocks([m(0, 10), m(100, 120)]);
    expect(blocks.map((b) => b.members.length)).toEqual([1, 1]);
  });

  it('groups overlapping markers into the union span', () => {
    const blocks = groupAdBlocks([m(1839.0, 2032.0), m(1992.8, 2160.3)]);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].start).toBe(1839.0);
    expect(blocks[0].end).toBe(2160.3);
  });

  it('chains three overlapping markers, sorting by start', () => {
    const blocks = groupAdBlocks([m(2097.1, 2334.9), m(1839.0, 2032.0), m(1992.8, 2160.3)]);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].members.map((x) => x.start)).toEqual([1839.0, 1992.8, 2097.1]);
    expect(blocks[0].end).toBe(2334.9);
  });

  it('keeps the larger end when a member is nested', () => {
    expect(groupAdBlocks([m(0, 100), m(10, 20)])[0].end).toBe(100);
  });

  it('joins at a 4.99 s gap and splits at exactly 5 s', () => {
    expect(groupAdBlocks([m(0, 10), m(14.99, 20)])).toHaveLength(1);
    expect(groupAdBlocks([m(0, 10), m(15, 20)])).toHaveLength(2);
  });
});
