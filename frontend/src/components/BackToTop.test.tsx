import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import BackToTop from './BackToTop';

function scrollTo(y: number) {
  Object.defineProperty(window, 'scrollY', { value: y, configurable: true });
  act(() => { window.dispatchEvent(new Event('scroll')); });
}

describe('BackToTop', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
    window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
    document.body.innerHTML = '<main id="main" tabindex="-1"><h1>Page</h1></main>';
    scrollTo(0);
  });
  afterEach(() => { document.body.innerHTML = ''; });

  const mount = () => render(<BackToTop />, { container: document.body.appendChild(document.createElement('div')) });

  it('is hidden at the top and shown past 1.5 viewports', () => {
    mount();
    expect(screen.queryByRole('button', { name: 'Back to top' })).toBeNull();
    scrollTo(1100);
    expect(screen.queryByRole('button', { name: 'Back to top' })).toBeNull();
    scrollTo(1300);
    expect(screen.getByRole('button', { name: 'Back to top' })).toBeTruthy();
  });

  it('stays out of the horizontal safe-area inset', () => {
    mount();
    scrollTo(3000);
    expect(screen.getByRole('button', { name: 'Back to top' }).className).toContain('right-[max(1rem,env(safe-area-inset-right))]');
  });

  it('scrolls to the top and focuses the page heading', () => {
    mount();
    scrollTo(3000);
    fireEvent.click(screen.getByRole('button', { name: 'Back to top' }));
    expect(window.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 0 }));
    expect(document.activeElement).toBe(document.querySelector('h1'));
  });

  it('steps aside while a control sits under it and returns once it is clear', async () => {
    mount();
    const control = document.createElement('button');
    const over = vi.fn(() => [control]);
    Object.defineProperty(document, 'elementsFromPoint', { value: over, configurable: true });
    scrollTo(3000);
    await act(async () => { await new Promise((r) => requestAnimationFrame(() => r(null))); });
    expect(screen.queryByRole('button', { name: 'Back to top' })).toBeNull();
    over.mockImplementation(() => []);
    scrollTo(3100);
    await act(async () => { await new Promise((r) => requestAnimationFrame(() => r(null))); });
    expect(screen.getByRole('button', { name: 'Back to top' })).toBeTruthy();
    Reflect.deleteProperty(document, 'elementsFromPoint');
  });

  it('is hidden while a dialog is open', async () => {
    mount();
    scrollTo(3000);
    expect(screen.getByRole('button', { name: 'Back to top' })).toBeTruthy();
    await act(async () => {
      const dlg = document.createElement('div');
      dlg.setAttribute('role', 'dialog');
      dlg.setAttribute('aria-modal', 'true');
      document.body.appendChild(dlg);
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(screen.queryByRole('button', { name: 'Back to top' })).toBeNull();
  });

  it('moves focus to the page heading when it unmounts while focused', () => {
    mount();
    scrollTo(3000);
    screen.getByRole('button', { name: 'Back to top' }).focus();
    scrollTo(0);
    expect(screen.queryByRole('button', { name: 'Back to top' })).toBeNull();
    expect(document.activeElement).toBe(document.querySelector('h1'));
  });

  it('leaves focus alone when the button is not focused as it hides', () => {
    mount();
    scrollTo(3000);
    const other = document.createElement('button');
    document.body.appendChild(other);
    other.focus();
    scrollTo(0);
    expect(document.activeElement).toBe(other);
  });

  it('stops observing the document once the button is hidden', () => {
    const observe = vi.spyOn(MutationObserver.prototype, 'observe');
    mount();
    expect(observe).not.toHaveBeenCalled();
    scrollTo(3000);
    expect(observe).toHaveBeenCalledTimes(1);
    const disconnect = vi.spyOn(MutationObserver.prototype, 'disconnect');
    scrollTo(0);
    expect(disconnect).toHaveBeenCalled();
    observe.mockRestore();
    disconnect.mockRestore();
  });
});
