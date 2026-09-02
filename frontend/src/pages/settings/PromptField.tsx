import FieldResetHeader from './FieldResetHeader';

interface PromptFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  helpText?: React.ReactNode;
  rows?: number;
  // Per-prompt reset (issue #626): shown beside the label whenever a handler
  // is supplied, disabled while the prompt is already at its default so the
  // affordance stays visible instead of disappearing.
  onReset?: () => void;
  isDefault?: boolean;
}

export default function PromptField({
  id,
  label,
  value,
  onChange,
  helpText,
  rows = 6,
  onReset,
  isDefault,
}: PromptFieldProps) {
  return (
    <div>
      <FieldResetHeader
        htmlFor={id}
        label={label}
        onReset={onReset}
        isDefault={isDefault}
      />
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm sm:rows-12"
      />
      {helpText && (
        <p className="mt-1 text-sm text-muted-foreground">{helpText}</p>
      )}
    </div>
  );
}
