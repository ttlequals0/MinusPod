import { ReactNode, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ChevronDown } from 'lucide-react';
import { focusRing } from './fieldStyles';
import { useOutsideClick } from '../hooks/useOutsideClick';

// w-56 menu; keep it at least this far inside the viewport edge.
const MENU_WIDTH_PX = 224;
const VIEWPORT_MARGIN_PX = 8;
// Below Tailwind's sm breakpoint the menu is centered on the screen under
// the trigger's row: a row that wraps on a phone leaves no side with room.
const PHONE_MAX_WIDTH_PX = 640;

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
  title?: string;
  chevronClassName?: string;
  /** Which edge of the menu aligns to the trigger. `auto` (default)
   *  opens leftward when the trigger has room on its left, otherwise
   *  rightward, so a button that wraps to either end of a row on a
   *  phone never clips off-screen. `left`/`right` force a side. */
  align?: 'left' | 'right' | 'auto';
}

function DropdownMenu({
  triggerLabel,
  triggerClassName,
  items,
  disabled,
  title,
  chevronClassName = 'w-4 h-4',
  align = 'auto',
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState<'left' | 'right'>('right');
  const [phoneTop, setPhoneTop] = useState<number | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  };

  // Arrow keys walk the items, Home/End jump to the ends.
  const onItemKeyDown = (e: ReactKeyboardEvent, i: number) => {
    const move = (next: number) => {
      e.preventDefault();
      itemRefs.current[(next + items.length) % items.length]?.focus();
    };
    if (e.key === 'ArrowDown') move(i + 1);
    else if (e.key === 'ArrowUp') move(i - 1);
    else if (e.key === 'Home') move(0);
    else if (e.key === 'End') move(items.length - 1);
    else if (e.key === 'Tab') close(false);
  };

  const onTriggerKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    setOpen(true);
    queueMicrotask(() => {
      const i = e.key === 'ArrowDown' ? 0 : items.length - 1;
      itemRefs.current[i]?.focus();
    });
  };

  // A trigger that becomes disabled (a mutation started elsewhere) must
  // not leave an open menu with live items behind it.
  const isOpen = open && !disabled;
  useOutsideClick(rootRef, isOpen, () => setOpen(false));

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen]);

  const toggle = () => {
    if (!open) {
      const rect = rootRef.current?.getBoundingClientRect();
      setPhoneTop(rect && window.innerWidth < PHONE_MAX_WIDTH_PX ? rect.bottom + 4 : null);
      if (align === 'auto') {
        setSide(rect && rect.right - MENU_WIDTH_PX < VIEWPORT_MARGIN_PX ? 'left' : 'right');
      }
    }
    setOpen(!open);
  };
  const resolvedSide = align === 'auto' ? side : align;
  const placement = phoneTop !== null
    ? 'fixed left-1/2 -translate-x-1/2'
    : `absolute ${resolvedSide === 'left' ? 'left-0' : 'right-0'} mt-1`;

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        onClick={toggle}
        onKeyDown={onTriggerKeyDown}
        disabled={disabled}
        className={`${triggerClassName} ${focusRing}`}
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={isOpen}
      >
        {triggerLabel}
        <ChevronDown className={`${chevronClassName} transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      {isOpen && (
        <div role="menu" style={phoneTop !== null ? { top: phoneTop } : undefined}
          className={`${placement} w-56 max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-lg z-10`}>
          {items.map((item, i) => {
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
                ref={(el) => { itemRefs.current[i] = el; }}
                role="menuitem"
                disabled={item.disabled}
                title={item.tooltip}
                onClick={() => {
                  close();
                  item.onClick();
                }}
                onKeyDown={(e) => onItemKeyDown(e, i)}
                className={`${cls} ${focusRing}`}
              >
                <span className="block text-sm font-medium text-foreground">{item.title}</span>
                {item.subtitle && (
                  <span className="block text-xs text-muted-foreground">{item.subtitle}</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default DropdownMenu;
