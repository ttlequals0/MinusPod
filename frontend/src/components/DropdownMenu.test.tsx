import { afterEach, describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DropdownMenu from './DropdownMenu';

const items = [{ title: 'One', onClick: vi.fn() }];

const PHONE_WIDTH_PX = 375;
const defaultWidth = window.innerWidth;
const setViewportWidth = (px: number) =>
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: px });

afterEach(() => setViewportWidth(defaultWidth));

describe('DropdownMenu', () => {
  it('closes an open menu when the trigger becomes disabled', async () => {
    const { rerender } = render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    expect(screen.getByRole('menu')).toBeTruthy();
    rerender(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} disabled />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('stays closed once the trigger is enabled again', async () => {
    const menu = (disabled?: boolean) => (
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} disabled={disabled} />);
    const { rerender } = render(menu());
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    rerender(menu(true));
    rerender(menu(false));
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

  it('leaves the visible label as the accessible name when only a tooltip is set', () => {
    render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} title="Act on this" />);
    const trigger = screen.getByRole('button', { name: 'Act' });
    expect(trigger.getAttribute('title')).toBe('Act on this');
    expect(trigger.getAttribute('aria-label')).toBeNull();
  });

  it('names an icon-only trigger from ariaLabel', () => {
    render(
      <DropdownMenu triggerLabel={<span />} triggerClassName="" items={items}
        title="Refresh all feeds" ariaLabel="Refresh all feeds" />);
    expect(screen.getByRole('button', { name: 'Refresh all feeds' })).toBeTruthy();
  });

  it('closes a phone menu when the page scrolls', async () => {
    setViewportWidth(PHONE_WIDTH_PX);
    render(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    fireEvent.scroll(window);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('closes a phone menu when the viewport resizes', async () => {
    setViewportWidth(PHONE_WIDTH_PX);
    render(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    fireEvent(window, new Event('resize'));
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('keeps an anchored menu open when a scroll happens at desktop width', async () => {
    render(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    fireEvent.scroll(window);
    expect(screen.queryByRole('menu')).toBeTruthy();
  });
});
