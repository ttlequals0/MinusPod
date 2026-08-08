interface ToggleSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
  // True when `checked` reflects an inherited default rather than an
  // explicit choice: the on-state renders muted instead of primary.
  muted?: boolean;
}

function ToggleSwitch({ checked, onChange, disabled, ariaLabel, muted }: ToggleSwitchProps) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      tabIndex={disabled ? -1 : 0}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
        checked ? (muted ? 'bg-muted' : 'bg-primary') : 'bg-secondary'
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
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </div>
  );
}

export default ToggleSwitch;
