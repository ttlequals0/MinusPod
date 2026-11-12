import { describe, expect, it } from 'vitest';
import { iconSizes, iconTarget, iconVisual } from './rowActionStyles';

const set = (s: string) => [...new Set(s.split(/\s+/))].sort();
const base = 'inline-flex items-center justify-center';
const tail = 'group shrink-0 touch-manipulation focus-visible:outline-hidden';
const visualBase = `${base} rounded transition-colors group-focus-visible:ring-2 group-focus-visible:ring-ring`;
const coarse = 'sm:[@media(pointer:coarse)]:h-11 sm:[@media(pointer:coarse)]:w-11';

describe('iconSizes', () => {
  it.each([
    ['row', 'sm:h-7 sm:w-7'],
    ['base', 'sm:h-8 sm:w-8'],
    ['field', 'sm:h-10 sm:w-10'],
  ] as const)('the %s size is 44 px below sm and on coarse pointers, then its own size from sm', (name, fine) => {
    expect(set(iconSizes[name].target)).toEqual(set(`${base} w-11 min-h-11 sm:min-h-0 ${fine} ${tail}`));
    expect(set(iconSizes[name].visual)).toEqual(set(`${visualBase} h-11 w-11 ${fine} ${coarse}`));
  });

  it('the full size is 44 px at every width', () => {
    expect(set(iconSizes.full.target)).toEqual(set(`${base} h-11 w-11 ${tail}`));
    expect(set(iconSizes.full.visual)).toEqual(set(`${visualBase} h-11 w-11`));
  });

  it('iconTarget and iconVisual are the row size', () => {
    expect(iconTarget).toBe(iconSizes.row.target);
    expect(iconVisual).toBe(iconSizes.row.visual);
  });
});
