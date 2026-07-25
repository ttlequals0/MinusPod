interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}

function Checkbox({ checked, onChange, disabled, className = '', ariaLabel }: CheckboxProps) {
  return (
    <label className={`relative inline-flex items-center ${disabled ? 'cursor-default opacity-50' : 'cursor-pointer'} ${className}`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        aria-label={ariaLabel}
        className="sr-only peer"
      />
      <div className={`h-4 w-4 rounded-sm border-2 transition-colors flex items-center justify-center ${
        checked
          ? 'bg-primary border-primary'
          : 'border-muted-foreground/40 bg-transparent hover:border-primary/60'
      } peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-1 peer-focus-visible:ring-offset-card`}>
        {checked && (
          <svg className="h-3 w-3 text-primary-foreground" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2.5 6L5 8.5L9.5 3.5" />
          </svg>
        )}
      </div>
    </label>
  );
}

export default Checkbox;
