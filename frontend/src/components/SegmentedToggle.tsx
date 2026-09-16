import { useRef } from 'react';
import { btnSecondary } from './buttonStyles';
import { focusRing } from './fieldStyles';

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  title?: string;
  // Accessible name when the label alone does not say what the segment does.
  ariaLabel?: string;
}

interface SegmentedToggleProps<T extends string> {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  // 'settings' is the compact ARIA radio row (roving tabindex, arrow keys);
  // 'toolbar' is the 44px button group in the toolbar and popover rows.
  variant?: 'settings' | 'toolbar';
  // Settings only: the value is still inherited, so it reads muted rather
  // than as an explicit choice.
  muted?: boolean;
  // Toolbar only: stretch the group and its segments to the container width,
  // for a popover row. A toolbar row sizes to its labels so it fits a phone.
  fill?: boolean;
  disabled?: boolean;
}

const VARIANTS = {
  settings: {
    group: 'inline-flex rounded-lg',
    segment: 'px-3 py-1.5 text-sm',
    divided: true,
  },
  toolbar: {
    group: 'flex h-11 rounded shrink-0',
    segment: 'inline-flex items-center justify-center h-11 px-2.5 sm:px-3 text-sm',
    divided: false,
  },
} as const;

// One button group, one selected segment. A portaled surface that needs
// ring-inset on focus applies it itself.
function SegmentedToggle<T extends string>({
  options, value, onChange, ariaLabel, variant = 'settings', muted, fill, disabled,
}: SegmentedToggleProps<T>) {
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
      className={`${sizing.group} border border-border overflow-hidden${fill ? ' w-full' : ''}`}
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
            disabled={disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={isRadio ? (e) => handleKeyDown(e, i) : undefined}
            className={`${sizing.segment}${fill ? ' flex-1 min-w-11' : ''} transition-colors disabled:opacity-50 ${
              sizing.divided && i > 0 ? 'border-l border-border' : ''
            } ${selected ? selectedFill : btnSecondary} ${focusRing}`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedToggle;
