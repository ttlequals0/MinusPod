import { useRef } from 'react';
import { btnSecondary } from './buttonStyles';

interface TriStateToggleOption<T extends string> {
  value: T;
  label: string;
}

interface TriStateToggleProps<T extends string> {
  value: T;
  options: readonly TriStateToggleOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  disabled?: boolean;
}

// Generic sibling of SegmentActionToggle for a fixed set of mutually
// exclusive string options (e.g. Inherit/On/Off). Same ARIA radiogroup +
// roving-tabindex pattern; kept separate because SegmentActionToggle
// hardcodes the remove/beep/keep action set.
function TriStateToggle<T extends string>({ value, options, onChange, ariaLabel, disabled }: TriStateToggleProps<T>) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);

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
    <div role="radiogroup" aria-label={ariaLabel} className="inline-flex rounded-lg border border-border overflow-hidden">
      {options.map((option, i) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => { buttonRefs.current[i] = el; }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={(e) => handleKeyDown(e, i)}
            className={`px-3 py-1.5 text-sm transition-colors disabled:opacity-50 ${i > 0 ? 'border-l border-border' : ''} ${
              selected ? 'bg-primary/10 text-primary font-medium' : btnSecondary
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export default TriStateToggle;
