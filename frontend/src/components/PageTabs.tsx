import { useRef, type ReactNode } from 'react';
import { focusRing } from './fieldStyles';
import { touchTarget } from './buttonStyles';

export const tabStrip = 'flex flex-wrap gap-1 border-b border-border mb-6';
// Sits at the bottom of a dialog header, with no margin below.
const tabStripFlush = 'flex flex-wrap gap-1 border-b border-border';
export const tabItem = `${touchTarget} shrink-0 px-2 sm:px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${focusRing}`;

export default function PageTabs<T extends string>({ id, label, tabs, active, onChange, flush = false }: {
  id: string;
  label: string;
  tabs: readonly { value: T; label: string; badge?: ReactNode; accessibleLabel?: string }[];
  active: T;
  onChange: (value: T) => void;
  flush?: boolean;
}) {
  const list = useRef<HTMLDivElement>(null);
  return (
    <div ref={list} role="tablist" aria-label={label} className={flush ? tabStripFlush : tabStrip}>
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
          className={`${tabItem} ${active === tab.value ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
          {tab.label}{tab.badge}
        </button>
      ))}
    </div>
  );
}
