import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';

// Hover switches to the solid destructive pair: a translucent fill would
// composite over the chip's blue and miss AA.
const removeButton = `text-c-blue-on-tint rounded px-0.5 hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50 ${focusRing}`;

/** One entry of an editable list: the value, plus the control that drops it. */
export function RemovableChip({ label, onRemove, disabled }: {
  label: string;
  onRemove: () => void;
  disabled?: boolean;
}) {
  return (
    <span className={`${badgeBase} inline-flex items-center gap-1 ${tint.blue}`}>
      {label}
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        className={removeButton}
        aria-label={`Remove ${label}`}
      >
        ×
      </button>
    </span>
  );
}
