import { SEGMENT_ACTIONS, SEGMENT_ACTION_LABELS, type SegmentAction } from '../utils/segmentCategory';
import SegmentedToggle from './SegmentedToggle';

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

const OPTIONS = SEGMENT_ACTIONS.map((action) => ({
  value: action,
  label: SEGMENT_ACTION_LABELS[action],
}));

// Three-way remove/beep/keep control shared by the global and per-feed
// segment-action matrices.
function SegmentActionToggle({ value, onChange, ariaLabel, disabled, muted }: SegmentActionToggleProps) {
  return (
    <SegmentedToggle
      options={OPTIONS}
      value={value}
      onChange={onChange}
      ariaLabel={ariaLabel}
      disabled={disabled}
      muted={muted}
    />
  );
}

export default SegmentActionToggle;
