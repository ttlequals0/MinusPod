import { useRef } from 'react';
import { btnPadY, btnSecondary } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { useGroupDisabled } from './DisabledGroup';

interface SegmentedOption<T extends string | number> {
  value: T;
  label: string;
  title?: string;
  // Accessible name when the label alone does not say what the segment does.
  ariaLabel?: string;
}

interface SegmentedToggleProps<T extends string | number> {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  // 'settings' is the compact ARIA radio row (roving tabindex, arrow keys);
  // 'toolbar' is the button group in toolbar and popover rows, sized like btnBase.
  variant?: 'settings' | 'toolbar';
  // Settings only: the value is still inherited, so it reads muted rather
  // than as an explicit choice.
  muted?: boolean;
  // Toolbar only: stretch the group and its segments to the container width,
  // for a popover row. A toolbar row sizes to its labels so it fits a phone.
  fill?: boolean;
  // Toolbar only: 44 px tall to match a filter row or popover field.
  tall?: boolean;
  disabled?: boolean;
  // Id of a hint describing the whole group.
  describedBy?: string;
  mobileTouchTarget?: boolean;
}

const VARIANTS = {
  settings: {
    group: 'inline-flex rounded-lg',
    segment: 'px-3 py-1.5 text-sm',
    divided: true,
  },
  toolbar: {
    group: 'flex rounded shrink-0',
    segment: `inline-flex items-center justify-center ${btnPadY} px-2.5 sm:px-3 text-sm`,
    divided: false,
  },
} as const;

// One button group, one selected segment. The focus ring is inset so the
// group's overflow-hidden cannot clip it.
function SegmentedToggle<T extends string | number>({
  options, value, onChange, ariaLabel, variant = 'settings', muted, fill, tall, disabled: ownDisabled,
  describedBy, mobileTouchTarget = false,
}: SegmentedToggleProps<T>) {
  const groupDisabled = useGroupDisabled();
  const disabled = ownDisabled || groupDisabled;
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const isRadio = variant === 'settings';
  const sizing = VARIANTS[variant];
  const selectedFill = isRadio
    ? (muted ? 'bg-muted text-muted-foreground font-medium' : 'bg-primary/10 text-primary font-medium')
    : 'bg-primary text-primary-foreground';

  const selectIndex = (index: number) => {
    onChange(options[index].value);
    // Roving tabindex needs DOM focus to follow the selection: a React
    // re-render alone won't move the browser's actual focus.
    buttonRefs.current[index]?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (disabled) return;
    const last = options.length - 1;
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
    <div
      role={isRadio ? 'radiogroup' : 'group'}
      aria-label={ariaLabel}
      aria-describedby={describedBy}
      // Inset outline, not a border, so the group is exactly the segments' height.
      className={`${sizing.group} outline outline-1 -outline-offset-1 outline-border overflow-hidden${fill ? ' w-full' : ''}${tall ? ' h-11' : ''}`}
    >
      {options.map((option, i) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={isRadio ? (el) => { buttonRefs.current[i] = el; } : undefined}
            type="button"
            role={isRadio ? 'radio' : undefined}
            aria-checked={isRadio ? selected : undefined}
            // Selection must reach a screen reader outside the radio pattern too.
            aria-pressed={isRadio ? undefined : selected}
            tabIndex={isRadio ? (selected ? 0 : -1) : undefined}
            aria-label={option.ariaLabel}
            title={option.title}
            // aria-disabled keeps the segments focusable so the group's description is still read.
            aria-disabled={disabled || undefined}
            onClick={() => { if (!disabled) onChange(option.value); }}
            onKeyDown={isRadio ? (e) => handleKeyDown(e, i) : undefined}
            className={`${sizing.segment}${fill ? ' flex-1 min-w-11' : ''}${mobileTouchTarget ? ' max-sm:min-h-11' : ''} transition-colors aria-disabled:opacity-50 aria-disabled:cursor-not-allowed ${
              sizing.divided && i > 0 ? 'border-l border-border' : ''
            } ${selected ? selectedFill : btnSecondary} ${focusRing} focus-visible:ring-inset`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedToggle;
