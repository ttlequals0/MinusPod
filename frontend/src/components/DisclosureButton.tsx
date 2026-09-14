import { ChevronDown, ChevronRight } from 'lucide-react';
import { focusRing } from './fieldStyles';

interface DisclosureButtonProps {
  expanded: boolean;
  onToggle: () => void;
  // Used as the visible label when showLabel is set, else as the aria-label.
  label: string;
  showLabel?: boolean;
}

// Row/card expander shared by the stats and processing-run tables. The hit
// area is a 44px mobile tap target around a 14px chevron; it shrinks to the
// table's row height from sm up, where pointers are precise.
function DisclosureButton({ expanded, onToggle, label, showLabel }: DisclosureButtonProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-label={showLabel ? undefined : label}
      className={`inline-flex items-center justify-center gap-1 min-h-11 min-w-11 sm:min-h-8 sm:min-w-8 rounded text-muted-foreground hover:text-foreground ${focusRing}`}
    >
      {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      {showLabel && <span className="text-xs">{label}</span>}
    </button>
  );
}

export default DisclosureButton;
