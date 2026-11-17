import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useMediaQuery } from './useMediaQuery';

describe('useMediaQuery', () => {
  const original = window.matchMedia;
  afterEach(() => {
    window.matchMedia = original;
  });

  it('subscribes once per query, not once per render', () => {
    const add = vi.fn();
    window.matchMedia = (() => ({ matches: true, addEventListener: add, removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
    const { rerender } = renderHook(({ q }) => useMediaQuery(q), { initialProps: { q: '(min-width: 640px)' } });
    rerender({ q: '(min-width: 640px)' });
    rerender({ q: '(min-width: 640px)' });
    expect(add).toHaveBeenCalledTimes(1);
    rerender({ q: '(min-width: 700px)' });
    expect(add).toHaveBeenCalledTimes(2);
  });
});
