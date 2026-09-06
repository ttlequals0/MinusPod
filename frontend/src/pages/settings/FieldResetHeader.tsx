import ConfirmResetButton from './ConfirmResetButton';

interface FieldResetHeaderProps {
  htmlFor: string;
  label: string;
  // Shown whenever a handler is supplied, disabled while the field is already
  // at its default so the affordance stays visible instead of disappearing.
  onReset?: () => void;
  isDefault?: boolean;
  // Accessible name, for pages carrying more than one "Reset" button.
  resetAriaLabel?: string;
}

/** A field label with its optional per-field Reset button. */
function FieldResetHeader({
  htmlFor, label, onReset, isDefault, resetAriaLabel,
}: FieldResetHeaderProps) {
  return (
    <div className="flex items-center justify-between gap-2 mb-2">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-foreground">
        {label}
      </label>
      {onReset && (
        <ConfirmResetButton
          label="Reset"
          ariaLabel={resetAriaLabel}
          onConfirm={onReset}
          size="compact"
          disabled={isDefault !== false}
          title={isDefault !== false ? 'Already the default' : undefined}
        />
      )}
    </div>
  );
}

export default FieldResetHeader;
