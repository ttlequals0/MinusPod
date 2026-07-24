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
function SegmentActionToggle({ value, onChange, ariaLabel, disabled, muted }: SegmentActionToggleProps) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="inline-flex rounded-lg border border-border overflow-hidden">
      {SEGMENT_ACTIONS.map((action, i) => {
        const selected = action === value;
        return (
          <button
            key={action}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(action)}
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
