import { ReactNode, useState } from 'react';
import { ChevronDown } from 'lucide-react';

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
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={disabled}
        className={triggerClassName}
        title={title}
        aria-label={title}
      >
        {triggerLabel}
        <ChevronDown className={`${chevronClassName} transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className={`absolute ${align === 'left' ? 'left-0' : 'right-0'} mt-1 w-56 max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-lg z-10`}>

          {items.map((item, i) => {
            const isFirst = i === 0;
            const isLast = i === items.length - 1;
            const cls = [
              'w-full px-4 py-2 text-left hover:bg-accent transition-colors',
              isFirst ? 'rounded-t-lg' : '',
              isLast ? 'rounded-b-lg' : '',
              isFirst ? '' : 'border-t border-border',
            ].filter(Boolean).join(' ');
            return (
              <button
                key={item.title}
                onClick={() => {
                  setOpen(false);
                  item.onClick();
                }}
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
