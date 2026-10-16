import { useEffect, useRef, type ReactNode } from 'react';
import { focusRing } from './fieldStyles';

export default function PageTabs<T extends string>({ id, label, tabs, active, onChange }: {
  id: string;
  label: string;
  tabs: readonly { value: T; label: string; badge?: ReactNode; accessibleLabel?: string }[];
  active: T;
  onChange: (value: T) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const tab = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!tab || !list.current) return;
    const container = list.current.getBoundingClientRect();
    const bounds = tab.getBoundingClientRect();
    if (bounds.left < container.left) list.current.scrollLeft -= container.left - bounds.left;
    if (bounds.right > container.right) list.current.scrollLeft += bounds.right - container.right;
  }, [active]);
  return (
    <div ref={list} role="tablist" aria-label={label} className="flex gap-1 border-b border-border mb-6 overflow-x-auto overflow-y-hidden">
      {tabs.map((tab, index) => (
        <button key={tab.value} type="button" role="tab" id={`${id}-tab-${tab.value}`}
          aria-controls={`${id}-panel-${tab.value}`} aria-selected={active === tab.value}
          aria-label={tab.accessibleLabel} tabIndex={active === tab.value ? 0 : -1}
          onClick={() => onChange(tab.value)}
          onKeyDown={(event) => {
            const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
              : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
                : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
            if (next === null) return;
            event.preventDefault();
            onChange(tabs[next].value);
            list.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
          }}
          className={`px-2 sm:px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${focusRing} ${active === tab.value ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
          {tab.label}{tab.badge}
        </button>
      ))}
    </div>
  );
}
