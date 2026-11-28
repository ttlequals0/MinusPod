import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SideCutoutToggle from '../pages/settings/SideCutoutToggle';
import { applyStoredSideCutout, readSideCutoutFill } from './sideCutout';

const ROOT = () => document.documentElement.getAttribute('data-side-cutout');
const NAME = 'Use full width beside a side cutout';

function stubInset(px: number) {
  const real = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((el: Element, pseudo?: string | null) => {
    const cs = real(el, pseudo);
    if ((el as HTMLElement).hasAttribute('data-side-inset-probe')) {
      return { paddingLeft: '0px', paddingRight: `${px}px` } as CSSStyleDeclaration;
    }
    return cs;
  });
}

describe('side cutout option', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-side-cutout');
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it('is off by default', () => {
    expect(readSideCutoutFill()).toBe(false);
    applyStoredSideCutout();
    expect(ROOT()).toBeNull();
  });

  it('toggling sets and removes the root attribute and persists', () => {
    stubInset(83);
    render(<SideCutoutToggle />);
    fireEvent.click(screen.getByRole('switch', { name: NAME }));
    expect(ROOT()).toBe('fill');
    expect(localStorage.getItem('sideCutout')).toBe('fill');
    fireEvent.click(screen.getByRole('switch', { name: NAME }));
    expect(ROOT()).toBeNull();
    expect(localStorage.getItem('sideCutout')).toBeNull();
  });

  it('is hidden when there is no side inset and the option is off', () => {
    stubInset(0);
    render(<SideCutoutToggle />);
    expect(screen.queryByRole('switch')).toBeNull();
  });

  it('is shown when the stored value is on even with no inset', () => {
    localStorage.setItem('sideCutout', 'fill');
    stubInset(0);
    render(<SideCutoutToggle />);
    expect(screen.getByRole('switch', { name: NAME }).getAttribute('aria-checked')).toBe('true');
    applyStoredSideCutout();
    expect(ROOT()).toBe('fill');
  });

  it('does not throw when storage fails', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied'); });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied'); });
    stubInset(83);
    expect(() => applyStoredSideCutout()).not.toThrow();
    render(<SideCutoutToggle />);
    expect(() => fireEvent.click(screen.getByRole('switch', { name: NAME }))).not.toThrow();
    expect(ROOT()).toBe('fill');
    expect(() => fireEvent.click(screen.getByRole('switch', { name: NAME }))).not.toThrow();
  });
});
