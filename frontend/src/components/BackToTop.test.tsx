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

  it('scrolls to the top and focuses the page heading', () => {
    mount();
    scrollTo(3000);
    fireEvent.click(screen.getByRole('button', { name: 'Back to top' }));
    expect(window.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 0 }));
    expect(document.activeElement).toBe(document.querySelector('h1'));
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
});
