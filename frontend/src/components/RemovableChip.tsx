import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';

// Hover switches to the solid destructive pair: a translucent fill would
// composite over the chip's blue and miss AA.
const removeButton = `min-h-11 min-w-11 sm:min-h-0 sm:min-w-0 shrink-0 text-c-blue-on-tint rounded px-0.5 hover:bg-destructive hover:text-destructive-foreground disabled:opacity-50 ${focusRing}`;

/** One entry of an editable list: the value, plus the control that drops it. */
export function RemovableChip({ label, onRemove, disabled }: {
  label: string;
  onRemove: () => void;
  disabled?: boolean;
}) {
  return (
    <span className={`${badgeBase} inline-flex max-w-full items-center gap-1 ${tint.blue}`}>
      <span className="min-w-0 break-all">{label}</span>
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
