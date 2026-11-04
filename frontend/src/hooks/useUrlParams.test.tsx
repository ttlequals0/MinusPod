import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import type { ReactNode } from 'react';
import { useUrlParams, useUrlSearch } from './useUrlParams';

const DEFAULTS = { page: '1', status: 'all' };

function wrapperFor(url: string) {
  return ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>;
}

function renderWith<T>(url: string, hook: () => T) {
  return renderHook(() => ({ value: hook(), search: useLocation().search }), { wrapper: wrapperFor(url) });
}

describe('useUrlParams', () => {
  it('drops the reset key on a filter write but keeps an explicit page', () => {
    const { result } = renderWith('/?page=3', () => useUrlParams(DEFAULTS, { resetKey: 'page' }));
    act(() => result.current.value.write({ status: 'failed' }));
    expect(result.current.search).toBe('?status=failed');
    act(() => result.current.value.write({ page: '4' }));
    expect(result.current.search).toBe('?status=failed&page=4');
  });

  it('readOneOf falls back for a value outside the allowed list', () => {
    const { result } = renderWith('/?status=bogus', () => useUrlParams(DEFAULTS));
    expect(result.current.value.readOneOf('status', ['all', 'failed'] as const, 'all')).toBe('all');
  });

  it('readOneOf returns an allowed value', () => {
    const { result } = renderWith('/?status=failed', () => useUrlParams(DEFAULTS));
    expect(result.current.value.readOneOf('status', ['all', 'failed'] as const, 'all')).toBe('failed');
  });

  it('readPage clamps to totalPages and to at least 1', () => {
    const { result } = renderWith('/?page=9', () => useUrlParams(DEFAULTS));
    expect(result.current.value.readPage('page')).toBe(9);
    expect(result.current.value.readPage('page', 4)).toBe(4);
    expect(result.current.value.readPage('page', 0)).toBe(1);
  });
});

describe('useUrlSearch', () => {
  afterEach(() => vi.useRealTimers());

  it('writes to the URL after the debounce and drops the page', () => {
    vi.useFakeTimers();
    const { result } = renderWith('/?page=2', () => useUrlSearch());
    act(() => result.current.value[1]('abc'));
    expect(result.current.search).toBe('?page=2');
    act(() => { vi.advanceTimersByTime(300); });
    expect(result.current.search).toBe('?q=abc');
  });

  it('does not write while disabled', () => {
    vi.useFakeTimers();
    const { result } = renderWith('/', () => useUrlSearch('q', { enabled: false }));
    act(() => result.current.value[1]('abc'));
    act(() => { vi.advanceTimersByTime(1000); });
    expect(result.current.search).toBe('');
  });

  it('re-syncs the input when the URL changes from outside', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => ({ search: useUrlSearch(), nav: useNavigate() }), {
      wrapper: wrapperFor('/?q=one'),
    });
    expect(result.current.search[0]).toBe('one');
    act(() => result.current.nav('/?q=two'));
    expect(result.current.search[0]).toBe('two');
  });
});

