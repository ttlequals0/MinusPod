import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRef, useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Popover from './Popover';
import { usePopoverTabs, type PopoverAlign, type PopoverCloseReason } from '../hooks/usePopover';
import {
  DESKTOP, PHONE, rect, resizePage, restoreViewport, scrollPage, setupPlacement, setViewport,
  stubPanelSize,
} from '../test/placement';

const PANEL = { width: 200, height: 120 };

interface HarnessProps {
  align?: PopoverAlign;
  /** false drops the anchor element, leaving the trigger with nothing to measure. */
  anchored?: boolean;
  anchorRect?: () => DOMRect;
  firstDisabled?: boolean;
  startOpen?: boolean;
  onClosed?: (reason: PopoverCloseReason) => void;
  /** Passed to the hook, not to the panel: the hook owns the id. */
  panelId?: string;
  'data-testid'?: string;
  'aria-label'?: string;
}

function Harness({
  align, anchored = true, anchorRect, firstDisabled, startOpen = true, onClosed, panelId, ...rest
}: HarnessProps) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(startOpen);
  const { triggerProps, popoverProps } = usePopoverTabs({
    open, setOpen, triggerRef, id: panelId,
  });

  const trigger = (
    <button ref={triggerRef} {...triggerProps} onClick={() => setOpen(!open)}>Trigger</button>
  );
  return (
    <>
      <button>Elsewhere</button>
      {anchored ? (
        <div
          ref={(el) => {
            anchorRef.current = el;
            if (el && anchorRect) el.getBoundingClientRect = anchorRect;
          }}
        >
          {trigger}
        </div>
      ) : trigger}
      <button>After</button>
      <Popover
        open={open}
        anchorRef={anchorRef}
        align={align}
        role="menu"
        {...popoverProps}
        {...rest}
        onClose={(reason) => { popoverProps.onClose(reason); onClosed?.(reason); }}
      >
        {open && (
          <>
            <button disabled={firstDisabled}>One</button>
            <button>Two</button>
          </>
        )}
      </Popover>
    </>
  );
}

const panel = () => screen.queryByRole('menu');
const trigger = () => screen.getByRole('button', { name: 'Trigger' });

/** A fixed bar of its own, the way the status bar and the save bar mark themselves. */
function addChromeBar(edge: 'top' | 'bottom', height: number) {
  const bar = document.createElement('div');
  bar.setAttribute('data-viewport-inset', edge);
  bar.getBoundingClientRect = rect({ height });
  document.body.appendChild(bar);
  return () => bar.remove();
}

beforeEach(() => setupPlacement(DESKTOP, PANEL));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  restoreViewport();
});

describe('Popover placement', () => {
  it('renders the panel as a child of the body', () => {
    render(<Harness />);
    expect(panel()!.parentElement).toBe(document.body);
  });

  it('insets the right edge from the document, not from window.innerWidth', () => {
    setViewport(1014, 768, 1024);
    stubPanelSize(PANEL.width, PANEL.height);
    render(<Harness align="right" anchorRect={rect({ left: 300, right: 500, top: 20, bottom: 40 })} />);
    expect(window.innerWidth).not.toBe(document.documentElement.clientWidth);
    expect(panel()!.style.right).toBe(`${document.documentElement.clientWidth - 500}px`);
  });

  it('aligns to the anchor right edge when the panel fits on its left', () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 20, bottom: 40 })} />);
    expect(panel()!.style.right).toBe(`${DESKTOP.width - 380}px`);
  });

  it('aligns to the anchor left edge when it has no room on its left', () => {
    render(<Harness anchorRect={rect({ left: 10, right: 90, top: 20, bottom: 40 })} />);
    expect(panel()!.style.left).toBe('10px');
  });

  it('clamps a forced alignment back inside the viewport', () => {
    render(<Harness align="left" anchorRect={rect({ left: 1000, right: 1016, top: 20, bottom: 40 })} />);
    expect(panel()!.style.left).toBe(`${DESKTOP.width - 8 - PANEL.width}px`);
  });

  it('re-places an open panel when align changes', () => {
    const anchor = rect({ left: 300, right: 380, top: 20, bottom: 40 });
    const { rerender } = render(<Harness align="right" anchorRect={anchor} />);
    expect(panel()!.style.right).toBe(`${DESKTOP.width - 380}px`);
    rerender(<Harness align="left" anchorRect={anchor} />);
    expect(panel()!.style.left).toBe('300px');
    expect(panel()!.style.right).toBe('');
  });

  it('opens above the anchor when the panel does not fit below', () => {
    render(<Harness anchorRect={rect({ left: 300, right: 500, top: 700, bottom: 740 })} />);
    expect(panel()!.style.bottom).toBe('72px');
    expect(panel()!.style.top).toBe('');
  });

  it('places from the content height, not the height its own cap leaves', () => {
    setupPlacement(DESKTOP, { width: PANEL.width, height: 500 });
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 400, bottom: 440 })} />);
    expect(panel()!.style.top).toBe('');
    expect(panel()!.style.bottom).toBe(`${DESKTOP.height - 400 + 4}px`);
  });

  it('caps the panel height at the room it has', () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 40, bottom: 600 })} />);
    expect(panel()!.style.maxHeight).toBe(`${DESKTOP.height - 600 - 12}px`);
  });

  it('slides the floored panel over the anchor rather than past the viewport edge', () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 20, bottom: 740 })} />);
    // 96px floor, so the top is pulled back to keep the bottom edge at 760.
    expect(panel()!.style.maxHeight).toBe('96px');
    expect(panel()!.style.top).toBe('664px');
  });

  it('centers on the screen under the anchor row on a phone', () => {
    setupPlacement(PHONE, PANEL);
    render(<Harness anchorRect={rect({ left: 200, right: 300, top: 100, bottom: 120 })} />);
    expect(panel()!.className).toContain('fixed left-1/2 -translate-x-1/2');
    expect(panel()!.style.top).toBe('124px');
  });

  it('reads the phone breakpoint off window.innerWidth, the box sm: matches', () => {
    setViewport(700, 768, 600);
    stubPanelSize(PANEL.width, PANEL.height);
    render(<Harness anchorRect={rect({ left: 200, right: 300, top: 100, bottom: 120 })} />);
    expect(panel()!.className).toContain('left-1/2 -translate-x-1/2');
  });

  it('spreads the caller attributes onto the panel without losing the placement', () => {
    render(<Harness data-testid="speed-menu" aria-label="Speed" anchorRect={rect({ left: 300, right: 500, top: 20, bottom: 40 })} />);
    expect(screen.getByTestId('speed-menu')).toBe(panel());
    expect(panel()!.style.top).toBe('44px');
  });

  it('draws the focus ring of its controls inset, clear of the scrolling edge', () => {
    render(<Harness />);
    expect(panel()!.className).toContain('[&_:focus-visible]:ring-inset');
  });
});

describe('Popover leaves the fixed chrome its room', () => {
  it('stops short of a bar that marks itself a bottom inset', () => {
    const remove = addChromeBar('bottom', 80);
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 40, bottom: 80 })} />);
    expect(panel()!.style.maxHeight).toBe(`${DESKTOP.height - 80 - 8 - 80 - 4}px`);
    remove();
  });

  it('stops short of a bar that marks itself a top inset', () => {
    const remove = addChromeBar('top', 60);
    setupPlacement(DESKTOP, { width: PANEL.width, height: 500 });
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 700, bottom: 740 })} />);
    expect(panel()!.style.maxHeight).toBe(`${700 - 4 - 60 - 8}px`);
    remove();
  });
});

describe('Popover follows its anchor', () => {
  it('re-places the panel when the page moves under the anchor', async () => {
    const anchor = rect({ left: 300, right: 380, top: 200, bottom: 240 });
    render(<Harness anchorRect={anchor} />);
    expect(panel()!.style.top).toBe('244px');
    trigger().parentElement!.getBoundingClientRect = rect({ left: 300, right: 380, top: 100, bottom: 140 });
    await scrollPage();
    expect(panel()!.style.top).toBe('144px');
  });

  it('re-places the panel when the viewport resizes', async () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 200, bottom: 240 })} />);
    setViewport(PHONE.width, PHONE.height);
    await resizePage();
    expect(panel()!.className).toContain('left-1/2 -translate-x-1/2');
  });

  it('keeps the panel open while the anchor stays in view', async () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 200, bottom: 240 })} />);
    await scrollPage();
    expect(panel()).toBeTruthy();
  });

  it('closes when the anchor scrolls out of view', async () => {
    const onClosed = vi.fn();
    render(<Harness onClosed={onClosed} anchorRect={rect({ left: 300, right: 380, top: 200, bottom: 240 })} />);
    trigger().parentElement!.getBoundingClientRect = rect({ left: 300, right: 380, top: -80, bottom: -40 });
    await scrollPage();
    expect(panel()).toBeNull();
    expect(onClosed).toHaveBeenCalledWith('anchor-hidden');
  });

  it('closes when the anchor scrolls off the side of the viewport', async () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 200, bottom: 240 })} />);
    trigger().parentElement!.getBoundingClientRect = rect({ left: 1100, right: 1180, top: 200, bottom: 240 });
    await scrollPage();
    expect(panel()).toBeNull();
  });

  it('closes when the anchor stops having a box at all', async () => {
    render(<Harness anchorRect={rect({ left: 300, right: 380, top: 200, bottom: 240 })} />);
    trigger().parentElement!.getClientRects = () => [] as unknown as DOMRectList;
    await scrollPage();
    expect(panel()).toBeNull();
  });

  it('starts a reopen unplaced instead of at the previous session clamp', async () => {
    const anchor = rect({ left: 20, right: 120, top: 600, bottom: 640 });
    const { rerender } = render(<Harness startOpen={false} anchorRect={anchor} />);
    await userEvent.click(trigger());
    expect(panel()!.style.maxHeight).toBe('588px');
    await userEvent.click(trigger());
    rerender(<Harness startOpen={false} anchored={false} anchorRect={anchor} />);
    await userEvent.click(trigger());
    expect(panel()!.style.maxHeight).toBe('');
  });

  it('drops its listeners and its observer when the panel closes', async () => {
    const disconnect = vi.fn();
    vi.stubGlobal('ResizeObserver', class {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = disconnect;
    });
    const removeListener = vi.spyOn(window, 'removeEventListener');
    render(<Harness />);
    await userEvent.keyboard('{Escape}');
    expect(disconnect).toHaveBeenCalled();
    expect(removeListener.mock.calls.map(([type]) => type)).toEqual(
      expect.arrayContaining(['scroll', 'resize']));
  });
});

describe('Popover dismissal', () => {
  it('closes on a click outside the anchor and the panel, leaving focus alone', async () => {
    const onClosed = vi.fn();
    render(<Harness onClosed={onClosed} />);
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' });
    await userEvent.click(elsewhere);
    expect(onClosed).toHaveBeenCalledWith('outside');
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(elsewhere);
  });

  it('stays open for a click inside the panel', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'Two' }));
    expect(panel()).toBeTruthy();
  });

  it('closes when focus lands outside without a click on the page', async () => {
    const onClosed = vi.fn();
    render(<Harness onClosed={onClosed} />);
    await act(async () => { screen.getByRole('button', { name: 'Elsewhere' }).focus(); });
    expect(onClosed).toHaveBeenCalledWith('outside');
    expect(panel()).toBeNull();
  });

  it('stays open when focus moves onto one of its own controls', async () => {
    render(<Harness />);
    await act(async () => { screen.getByRole('button', { name: 'One' }).focus(); });
    expect(panel()).toBeTruthy();
  });

  it('closes on Escape without letting the key reach a window listener', async () => {
    const onWindowKey = vi.fn();
    window.addEventListener('keydown', onWindowKey);
    render(<Harness />);
    await userEvent.keyboard('{Escape}');
    expect(panel()).toBeNull();
    expect(onWindowKey).not.toHaveBeenCalled();
    window.removeEventListener('keydown', onWindowKey);
  });

  it('refocuses the trigger on Escape', async () => {
    render(<Harness />);
    await userEvent.keyboard('{Escape}');
    expect(document.activeElement).toBe(trigger());
  });
});

describe('Popover keyboard routing', () => {
  it('points aria-expanded and aria-controls at the open panel', async () => {
    render(<Harness startOpen={false} />);
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(trigger().getAttribute('aria-controls')).toBeNull();
    await userEvent.click(trigger());
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(trigger().getAttribute('aria-controls')).toBe(panel()!.id);
  });

  it('takes the panel id from the caller when one is given', () => {
    render(<Harness panelId="speed-menu" />);
    expect(panel()!.id).toBe('speed-menu');
    expect(trigger().getAttribute('aria-controls')).toBe('speed-menu');
  });

  it('opens on ArrowDown with the first control focused', async () => {
    render(<Harness startOpen={false} />);
    trigger().focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'One' }));
  });

  it('opens on ArrowUp with the last control focused', async () => {
    render(<Harness startOpen={false} />);
    trigger().focus();
    await userEvent.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Two' }));
  });

  it('moves focus into the panel when Tab is pressed on the open trigger', async () => {
    render(<Harness />);
    trigger().focus();
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'One' }));
  });

  it('skips a disabled first control when Tab enters the panel', async () => {
    render(<Harness firstDisabled />);
    trigger().focus();
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Two' }));
  });

  it('closes on Tab past the last control and carries focus on past the trigger', async () => {
    render(<Harness />);
    screen.getByRole('button', { name: 'Two' }).focus();
    await userEvent.tab();
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'After' }));
  });

  it('closes on Shift+Tab from the first control and carries focus on backward', async () => {
    render(<Harness />);
    screen.getByRole('button', { name: 'One' }).focus();
    await userEvent.tab({ shift: true });
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Elsewhere' }));
  });

  it('closes on Shift+Tab from the open trigger and lets focus move backward', async () => {
    render(<Harness />);
    trigger().focus();
    await userEvent.tab({ shift: true });
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Elsewhere' }));
  });
});
