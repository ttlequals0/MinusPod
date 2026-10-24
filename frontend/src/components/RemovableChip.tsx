import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';

// Negative margins keep the 44px touch target without inflating the chip.
// Hover switches to the solid destructive pair: a translucent fill would
// composite over the chip's blue and miss AA.
const removeButton = `min-h-11 min-w-11 -my-3.5 -mr-2 sm:min-h-0 sm:min-w-0 sm:my-0 sm:mr-0 shrink-0 text-c-blue-on-tint rounded px-0.5 hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50 ${focusRing}`;

/** One entry of an editable list: the value, plus the control that drops it. */
export function RemovableChip({ label, onRemove, disabled }: {
  label: string;
  onRemove: () => void;
  disabled?: boolean;
}) {
  return (
    <span className={`${badgeBase} max-w-full gap-1 ${tint.blue}`}>
      <span className="min-w-0 break-words">{label}</span>
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
