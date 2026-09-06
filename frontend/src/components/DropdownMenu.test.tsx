import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DropdownMenu from './DropdownMenu';

const items = [{ title: 'One', onClick: vi.fn() }];

describe('DropdownMenu', () => {
  it('closes an open menu when the trigger becomes disabled', async () => {
    const { rerender } = render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    expect(screen.getByRole('menu')).toBeTruthy();
    rerender(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} disabled />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('opens rightward when the trigger has no room on its left', async () => {
    const { container } = render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    const root = container.firstChild as HTMLElement;
    root.getBoundingClientRect = () => ({ left: 10, right: 90 } as DOMRect);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    expect(screen.getByRole('menu').className).toContain('left-0');
  });

  it('opens leftward when the trigger sits at the right edge', async () => {
    const { container } = render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    const root = container.firstChild as HTMLElement;
    root.getBoundingClientRect = () => ({ left: 300, right: 380 } as DOMRect);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    expect(screen.getByRole('menu').className).toContain('right-0');
  });

  it('centers on the screen under the trigger on a phone', async () => {
    const width = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
    try {
      const { container } = render(
        <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
      const root = container.firstChild as HTMLElement;
      root.getBoundingClientRect = () => ({ left: 200, right: 300, bottom: 120 } as DOMRect);
      await userEvent.click(screen.getByRole('button', { name: 'Act' }));
      const menu = screen.getByRole('menu');
      expect(menu.className).toContain('fixed left-1/2 -translate-x-1/2');
      expect(menu.style.top).toBe('124px');
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    }
  });
});
