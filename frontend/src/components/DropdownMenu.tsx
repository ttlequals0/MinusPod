import { ReactNode, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ChevronDown } from 'lucide-react';
import { focusRing } from './fieldStyles';

export interface DropdownMenuItem {
  title: string;
  subtitle?: string;
  onClick: () => void;
}

interface DropdownMenuProps {
  triggerLabel: ReactNode;
  triggerClassName: string;
  items: DropdownMenuItem[];
  disabled?: boolean;
  title?: string;
  chevronClassName?: string;
  /** Which edge of the menu aligns to the trigger. `right` (default)
   * opens the menu leftward -- correct when the trigger sits on the
   *  right side of a row. Use `left` for triggers on the left side so
   *  the menu opens rightward and doesn't clip off-screen on mobile. */
  align?: 'left' | 'right';
}

function DropdownMenu({
  triggerLabel,
  triggerClassName,
  items,
  disabled,
  title,
  chevronClassName = 'w-4 h-4',
  align = 'right',
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
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

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        onClick={() => setOpen(!open)}
        onKeyDown={onTriggerKeyDown}
        disabled={disabled}
        className={`${triggerClassName} ${focusRing}`}
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {triggerLabel}
        <ChevronDown className={`${chevronClassName} transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div role="menu" className={`absolute ${align === 'left' ? 'left-0' : 'right-0'} mt-1 w-56 max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-lg z-10`}>
          {items.map((item, i) => {
            const isFirst = i === 0;
            const isLast = i === items.length - 1;
            const cls = [
              'w-full px-4 py-2 text-left hover:bg-accent transition-colors',
              focusRing,
              isFirst ? 'rounded-t-lg' : '',
              isLast ? 'rounded-b-lg' : '',
              isFirst ? '' : 'border-t border-border',
            ].filter(Boolean).join(' ');
            return (
              <button
                key={item.title}
                ref={(el) => { itemRefs.current[i] = el; }}
                role="menuitem"
                onClick={() => {
                  close();
                  item.onClick();
                }}
                onKeyDown={(e) => onItemKeyDown(e, i)}
                className={cls}
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
