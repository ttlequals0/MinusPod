import { useRef } from 'react';
import { SEGMENT_ACTIONS, SEGMENT_ACTION_LABELS, type SegmentAction } from '../utils/segmentCategory';
import { btnSecondary } from './buttonStyles';

interface SegmentActionToggleProps {
  value: SegmentAction;
  onChange: (action: SegmentAction) => void;
  ariaLabel: string;
  disabled?: boolean;
  // True while the value is an inherited default rather than an explicit
  // choice: the selected option renders muted instead of primary so an
  // untouched row visibly reads as "not yet overridden".
  muted?: boolean;
}

// Three-way remove/beep/keep control shared by the global segment-action
// matrix and the per-feed override matrix. No segmented/button-group
// component exists elsewhere in the app, so this joins three btnSecondary-
// style buttons into one radiogroup, with the selected option picking up
// the same bg-primary/10 text-primary treatment used for other selected
// states (e.g. the Appearance theme picker).
//
// Implements the standard ARIA radiogroup keyboard pattern: one tab stop
// (roving tabindex -- only the selected option is in tab order), Left/Up
// and Right/Down move and commit the selection with wraparound, Home/End
// jump to the first/last option.
function SegmentActionToggle({ value, onChange, ariaLabel, disabled, muted }: SegmentActionToggleProps) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const selectIndex = (index: number) => {
    const action = SEGMENT_ACTIONS[index];
    onChange(action);
    // Roving tabindex requires DOM focus to follow the selection, not just
    // the aria-checked/tabIndex props -- React re-render alone won't move
    // the browser's actual focus to the newly-selected button.
    buttonRefs.current[index]?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (disabled) return;
    const last = SEGMENT_ACTIONS.length - 1;
    switch (e.key) {
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault();
        selectIndex(index === 0 ? last : index - 1);
        break;
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault();
        selectIndex(index === last ? 0 : index + 1);
        break;
      case 'Home':
        e.preventDefault();
        selectIndex(0);
        break;
      case 'End':
        e.preventDefault();
        selectIndex(last);
        break;
      default:
        break;
    }
  };

  return (
    <div role="radiogroup" aria-label={ariaLabel} className="inline-flex rounded-lg border border-border overflow-hidden">
      {SEGMENT_ACTIONS.map((action, i) => {
        const selected = action === value;
        return (
          <button
            key={action}
            ref={(el) => { buttonRefs.current[i] = el; }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(action)}
            onKeyDown={(e) => handleKeyDown(e, i)}
            className={`px-3 py-1.5 text-sm transition-colors disabled:opacity-50 ${i > 0 ? 'border-l border-border' : ''} ${
              selected
                ? muted
                  ? 'bg-muted text-muted-foreground font-medium'
                  : 'bg-primary/10 text-primary font-medium'
                : btnSecondary
            }`}
          >
            {SEGMENT_ACTION_LABELS[action]}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentActionToggle;
