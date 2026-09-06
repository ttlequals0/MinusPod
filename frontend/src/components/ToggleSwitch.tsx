import { focusRing } from './fieldStyles';

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
}

function ToggleSwitch({ checked, onChange, disabled, ariaLabel }: ToggleSwitchProps) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      tabIndex={disabled ? -1 : 0}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${focusRing} ${
        checked ? 'bg-primary' : 'bg-secondary'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          onChange(!checked);
        }
      }}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-primary-foreground transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-[2px]'
        }`}
      />
    </div>
  );
}

export default ToggleSwitch;
