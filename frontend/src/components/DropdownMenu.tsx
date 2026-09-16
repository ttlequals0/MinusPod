import { ReactNode, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ChevronDown } from 'lucide-react';
import { usePopoverTabs } from '../hooks/usePopover';
import { focusRing } from './fieldStyles';
import Popover from './Popover';

export interface DropdownMenuItem {
  title: string;
  subtitle?: string;
  onClick: () => void;
  disabled?: boolean;
  /** Native tooltip on the item. */
  tooltip?: string;
}

interface DropdownMenuProps {
  triggerLabel: ReactNode;
  triggerClassName: string;
  items: DropdownMenuItem[];
  disabled?: boolean;
  /** Native tooltip on the trigger. */
  title?: string;
  /** Accessible name; only for a trigger with no visible text at some breakpoint. */
  ariaLabel?: string;
  chevronClassName?: string;
  /** Which menu edge aligns to the trigger. `auto` (default) opens leftward when
   *  the menu fits on the trigger's left, otherwise rightward; `left`/`right` force a side. */
  align?: 'left' | 'right' | 'auto';
}

const ROVING_KEYS = ['ArrowDown', 'ArrowUp', 'Home', 'End'];

function DropdownMenu({
  triggerLabel,
  triggerClassName,
  items,
  disabled,
  title,
  ariaLabel,
  chevronClassName = 'w-4 h-4',
  align = 'auto',
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // A trigger that becomes disabled (a mutation started elsewhere) must not leave
  // an open menu behind it, nor pop that menu back open once it is re-enabled.
  if (disabled && open) setOpen(false);
  const isOpen = open && !disabled;

  const { controls, closeAndRefocus, triggerProps, popoverProps } = usePopoverTabs({
    open: isOpen, setOpen, triggerRef,
  });

  // Arrow keys walk the enabled items and wrap; Home/End jump to the ends.
  const onItemKeyDown = (e: ReactKeyboardEvent) => {
    if (!ROVING_KEYS.includes(e.key)) return;
    e.preventDefault();
    const enabled = controls();
    if (!enabled.length) return;
    const pos = enabled.indexOf(document.activeElement as HTMLElement);
    const next = e.key === 'Home' ? 0
      : e.key === 'End' ? enabled.length - 1
        : pos + (e.key === 'ArrowDown' ? 1 : -1);
    enabled[(next + enabled.length) % enabled.length].focus();
  };

  return (
    <div ref={rootRef}>
      <button
        ref={triggerRef}
        {...triggerProps}
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        className={`${triggerClassName} ${focusRing}`}
        title={title}
        aria-label={ariaLabel}
        aria-haspopup="menu"
      >
        {triggerLabel}
        <ChevronDown className={`${chevronClassName} transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      <Popover
        open={isOpen}
        anchorRef={rootRef}
        align={align}
        role="menu"
        className="w-56"
        {...popoverProps}
      >
        {isOpen && items.map((item, i) => {
          const isFirst = i === 0;
          const isLast = i === items.length - 1;
          const cls = [
            'w-full px-4 py-2 text-left hover:bg-accent transition-colors',
            'disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent',
            isFirst ? 'rounded-t-lg' : '',
            isLast ? 'rounded-b-lg' : '',
            isFirst ? '' : 'border-t border-border',
          ].filter(Boolean).join(' ');
          return (
            <button
              key={item.title}
              role="menuitem"
              disabled={item.disabled}
              title={item.tooltip}
              onClick={() => {
                closeAndRefocus();
                item.onClick();
              }}
              onKeyDown={onItemKeyDown}
              className={`${cls} ${focusRing}`}
            >
              <span className="block text-sm font-medium text-foreground">{item.title}</span>
              {item.subtitle && (
                <span className="block text-xs text-muted-foreground">{item.subtitle}</span>
              )}
            </button>
          );
        })}
      </Popover>
    </div>
  );
}

export default DropdownMenu;
