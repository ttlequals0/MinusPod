import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRef } from 'react';
import { useSectionSearch } from './useSectionSearch';

let region: HTMLDivElement;
let outside: HTMLDivElement;

beforeEach(() => {
  outside = document.createElement('div');
  outside.innerHTML = '<div data-search-key="outside">Retention outside the region</div>';
  region = document.createElement('div');
  region.innerHTML =
    '<div data-search-key="alpha">Retention days</div><div data-search-key="beta">Queue priority</div>';
  document.body.append(outside, region);
});

afterEach(() => {
  outside.remove();
  region.remove();
});

function setup() {
  return renderHook(() => useSectionSearch(useRef<HTMLElement | null>(region)));
}

describe('useSectionSearch', () => {
  it('starts with no active search', () => {
    const { result } = setup();
    expect(result.current.matchKeys).toBeNull();
    expect(result.current.query).toBe('');
  });

  it('matches cards inside the region case-insensitively', () => {
    const { result } = setup();
    act(() => result.current.run('  RETENTION '));
    expect([...result.current.matchKeys!]).toEqual(['alpha']);
    expect(result.current.query).toBe('  RETENTION ');
  });

  it('returns an empty set when nothing matches', () => {
    const { result } = setup();
    act(() => result.current.run('nonexistent'));
    expect(result.current.matchKeys!.size).toBe(0);
  });

  it('treats a whitespace-only query as no search', () => {
    const { result } = setup();
    act(() => result.current.run('   '));
    expect(result.current.matchKeys).toBeNull();
  });

  it('keeps the same match set when a longer query matches the same cards', () => {
    const { result } = setup();
    act(() => result.current.run('ret'));
    const first = result.current.matchKeys;
    act(() => result.current.run('reten'));
    expect(result.current.query).toBe('reten');
    expect(result.current.matchKeys).toBe(first);
    act(() => result.current.run('queue'));
    expect(result.current.matchKeys).not.toBe(first);
  });

  it('clear resets the query and matches', () => {
    const { result } = setup();
    act(() => result.current.run('queue'));
    expect([...result.current.matchKeys!]).toEqual(['beta']);
    act(() => result.current.clear());
    expect(result.current.matchKeys).toBeNull();
    expect(result.current.query).toBe('');
  });

  describe('highlights', () => {
    let highlights: { set: ReturnType<typeof vi.fn>; delete: ReturnType<typeof vi.fn> };

    beforeEach(() => {
      highlights = { set: vi.fn(), delete: vi.fn() };
      vi.stubGlobal('CSS', { highlights });
      vi.stubGlobal('Highlight', class {});
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('sets the highlight on a match and deletes it on clear', () => {
      const { result } = setup();
      act(() => result.current.run('queue'));
      expect(highlights.set).toHaveBeenCalledWith('settings-search', expect.any(Object));
      highlights.delete.mockClear();
      act(() => result.current.clear());
      expect(highlights.delete).toHaveBeenCalledWith('settings-search');
    });

    it('deletes the highlight when unmounted while searching', () => {
      const { result, unmount } = setup();
      act(() => result.current.run('queue'));
      highlights.delete.mockClear();
      unmount();
      expect(highlights.delete).toHaveBeenCalledWith('settings-search');
    });
  });
});
