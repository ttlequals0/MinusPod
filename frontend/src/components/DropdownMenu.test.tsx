import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DropdownMenu from './DropdownMenu';
import { DESKTOP, rect, restoreViewport, setupPlacement } from '../test/placement';

const items = [{ title: 'One', onClick: vi.fn() }];
const threeItems = [
  { title: 'One', onClick: vi.fn() },
  { title: 'Two', onClick: vi.fn() },
  { title: 'Three', onClick: vi.fn() },
];
const MENU_WIDTH_PX = 224;

function renderMenu(props: Partial<Parameters<typeof DropdownMenu>[0]> = {}) {
  const view = render(
    <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} {...props} />);
  return { ...view, root: view.container.firstChild as HTMLElement };
}

const openMenu = () => userEvent.click(screen.getByRole('button', { name: 'Act' }));
const item = (name: string) => screen.getByRole('menuitem', { name });

beforeEach(() => setupPlacement(DESKTOP, { width: MENU_WIDTH_PX, height: 200 }));
afterEach(() => {
  vi.restoreAllMocks();
  restoreViewport();
});

describe('DropdownMenu', () => {
  it('closes an open menu when the trigger becomes disabled', async () => {
    const { rerender } = render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await openMenu();
    expect(screen.getByRole('menu')).toBeTruthy();
    rerender(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} disabled />);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('stays closed once the trigger is enabled again', async () => {
    const menu = (disabled?: boolean) => (
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} disabled={disabled} />);
    const { rerender } = render(menu());
    await openMenu();
    rerender(menu(true));
    rerender(menu(false));
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('passes align through to the placement', async () => {
    const { root } = renderMenu({ align: 'left' });
    root.getBoundingClientRect = rect({ left: 300, right: 380, top: 20, bottom: 40 });
    await openMenu();
    // `auto` would take the right edge here, since the menu fits on the trigger's left.
    expect(screen.getByRole('menu').style.left).toBe('300px');
  });

  it('announces the trigger and its items as a menu', async () => {
    renderMenu({ items: threeItems });
    expect(screen.getByRole('button', { name: 'Act' }).getAttribute('aria-haspopup')).toBe('menu');
    await openMenu();
    expect(screen.getAllByRole('menuitem')).toHaveLength(3);
  });

  it('walks the items with the arrow keys and wraps at the ends', async () => {
    renderMenu({ items: threeItems });
    await openMenu();
    item('One').focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(item('Two'));
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(item('One'));
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(item('Three'));
  });

  it('jumps to the ends of the menu with Home and End', async () => {
    renderMenu({ items: threeItems });
    await openMenu();
    item('One').focus();
    await userEvent.keyboard('{End}');
    expect(document.activeElement).toBe(item('Three'));
    await userEvent.keyboard('{Home}');
    expect(document.activeElement).toBe(item('One'));
  });

  it('walks past a disabled item and leaves it unclickable', async () => {
    const onClick = vi.fn();
    renderMenu({ items: [threeItems[0], { title: 'Two', onClick, disabled: true }, threeItems[2]] });
    await openMenu();
    expect((item('Two') as HTMLButtonElement).disabled).toBe(true);
    item('One').focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(item('Three'));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('routes Tab from the open trigger into the menu', async () => {
    renderMenu({ items: threeItems });
    await openMenu();
    await userEvent.tab();
    expect(document.activeElement).toBe(item('One'));
  });

  it('leaves the visible label as the accessible name when only a tooltip is set', () => {
    renderMenu({ title: 'Act on this' });
    const trigger = screen.getByRole('button', { name: 'Act' });
    expect(trigger.getAttribute('title')).toBe('Act on this');
    expect(trigger.getAttribute('aria-label')).toBeNull();
  });

  it('names an icon-only trigger from ariaLabel', () => {
    renderMenu({ triggerLabel: <span />, title: 'Refresh all feeds', ariaLabel: 'Refresh all feeds' });
    expect(screen.getByRole('button', { name: 'Refresh all feeds' })).toBeTruthy();
  });

  it('runs an item clicked in the portaled menu', async () => {
    const onClick = vi.fn();
    renderMenu({ items: [{ title: 'One', onClick }] });
    await openMenu();
    await userEvent.click(item('One'));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
