import { useRef, type ReactNode } from 'react';
import { focusRing } from './fieldStyles';
import { touchTarget } from './buttonStyles';
import { countPill } from './badgeStyles';

export const tabStrip = 'flex flex-wrap gap-1 border-b border-border mb-6';
// Sits at the bottom of a dialog header, with no margin below.
const tabStripFlush = 'flex flex-wrap gap-1 border-b border-border';
export const tabItem = `${touchTarget} shrink-0 px-2 sm:px-4 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${focusRing}`;

// Filled-pill variant, the Settings category bar style at page scale.
const pillStrip = 'flex flex-wrap gap-1.5 mb-6';
const pillItem = `${touchTarget} shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs font-medium whitespace-nowrap transition-colors ${focusRing}`;

export default function PageTabs<T extends string>({ id, label, tabs, active, onChange, flush = false, variant = 'underline' }: {
  id: string;
  label: string;
  tabs: readonly { value: T; label: string; badge?: ReactNode; count?: number; countTint?: string; accessibleLabel?: string }[];
  active: T;
  onChange: (value: T) => void;
  flush?: boolean;
  variant?: 'underline' | 'pills';
}) {
  const pills = variant === 'pills';
  const list = useRef<HTMLDivElement>(null);
  return (
    <div ref={list} role="tablist" aria-label={label} className={pills ? pillStrip : flush ? tabStripFlush : tabStrip}>
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
          className={pills
            ? `${pillItem} ${active === tab.value ? 'bg-primary text-primary-foreground border-primary font-semibold' : 'bg-card text-muted-foreground border-border hover:text-foreground'}`
            : `${tabItem} ${active === tab.value ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
          {tab.label}{tab.badge}
          {tab.count !== undefined && (
            <span className={`${countPill} ${pills ? '' : 'ml-1.5'} ${active === tab.value && pills ? 'bg-background/25 text-primary-foreground' : tab.countTint ?? 'bg-secondary text-foreground'}`}>{tab.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
