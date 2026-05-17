import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useLocalStorageState } from '../hooks/useLocalStorageState';

interface CollapsibleSectionProps {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
  headerRight?: ReactNode;
  storageKey?: string;
  onToggle?: (isOpen: boolean) => void;
}

function CollapsibleSection({
  title,
  subtitle,
  defaultOpen = false,
  children,
  headerRight,
  storageKey,
  onToggle,
}: CollapsibleSectionProps) {
  const resolvedKey = storageKey || `settings-section-${title.toLowerCase().replace(/\s+/g, '-')}`;

  const [isOpen, setIsOpen] = useLocalStorageState<boolean>(resolvedKey, defaultOpen);

  const contentRef = useRef<HTMLDivElement>(null);
  const [maxHeight, setMaxHeight] = useState<string>(isOpen ? 'none' : '0px');

  useEffect(() => {
    if (isOpen) {
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
        const timer = setTimeout(() => setMaxHeight('none'), 300);
        return () => clearTimeout(timer);
      }
    } else {
      // Collapse: first set explicit height, then 0
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
        requestAnimationFrame(() => {
          setMaxHeight('0px');
        });
      }
    }
  }, [isOpen]);

  // Intentionally no dependency array: re-measures content height after every
  // render so dynamic child changes (e.g. conditional content, async loads)
  // are reflected in the animation. Cost is negligible (single DOM read).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (isOpen && maxHeight !== 'none') {
      const el = contentRef.current;
      if (el) {
        setMaxHeight(`${el.scrollHeight}px`);
      }
    }
  });

  return (
    <div className="bg-card rounded-lg border border-border">
      <button
        type="button"
        onClick={() => {
          const next = !isOpen;
          setIsOpen(next);
          onToggle?.(next);
        }}
        className="w-full flex items-center justify-between p-4 sm:p-6 text-left"
      >
        <div className="flex-1 min-w-0">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          {subtitle && (
            <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2 ml-4 shrink-0">
          {headerRight && (
            <div onClick={(e) => e.stopPropagation()}>
              {headerRight}
            </div>
          )}
          <svg
            className={`w-5 h-5 text-muted-foreground transition-transform duration-200 ${
              isOpen ? 'rotate-180' : ''
            }`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      <div
        ref={contentRef}
        style={{ maxHeight }}
        className={`overflow-hidden ${maxHeight !== 'none' && maxHeight !== '0px' ? 'transition-[max-height] duration-300 ease-in-out' : ''}`}
      >
        <div className="px-4 pb-4 sm:px-6 sm:pb-6">
          {children}
        </div>
      </div>
    </div>
  );
}

export default CollapsibleSection;
