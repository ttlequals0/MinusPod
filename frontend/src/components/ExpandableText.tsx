import { ReactNode, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { focusRing } from './fieldStyles';

// Literal class names so Tailwind's scanner emits them; a template string
// built from clampLines would not be generated.
const CLAMP_CLASS: Record<number, string> = {
  2: 'line-clamp-2',
  3: 'line-clamp-3',
  4: 'line-clamp-4',
  5: 'line-clamp-5',
  6: 'line-clamp-6',
};

interface ExpandableTextProps {
  children: ReactNode;
  /** Lines shown while collapsed. Must be a key of CLAMP_CLASS. */
  clampLines?: 2 | 3 | 4 | 5 | 6;
  className?: string;
  /** Label for the control; the noun makes it clear what expands. */
  label?: string;
}

/**
 * Clamps long prose to a few lines and offers a control to see the rest.
 *
 * The control only appears when the text actually overflows, so short entries
 * stay a plain paragraph with nothing extra to read past.
 */
function ExpandableText({ children, clampLines = 4, className, label = 'text' }: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const bodyId = useId();

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // Only meaningful while clamped; expanded height always equals scrollHeight.
    if (expanded) return;
    setOverflows(el.scrollHeight - el.clientHeight > 1);
  }, [expanded]);

  useLayoutEffect(measure, [measure, children]);

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);

  const clamp = expanded ? '' : (CLAMP_CLASS[clampLines] ?? CLAMP_CLASS[4]);

  return (
    <div className={className}>
      <div id={bodyId} ref={ref} className={`wrap-break-word ${clamp}`.trim()}>
        {children}
      </div>
      {(overflows || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls={bodyId}
          className={`mt-1 text-xs text-primary hover:underline ${focusRing}`}
        >
          {expanded ? `Show less ${label}` : `Show full ${label}`}
        </button>
      )}
    </div>
  );
}

export default ExpandableText;
