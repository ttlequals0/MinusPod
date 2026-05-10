interface PromptFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  helpText?: React.ReactNode;
  rows?: number;
}

export default function PromptField({
  id,
  label,
  value,
  onChange,
  helpText,
  rows = 6,
}: PromptFieldProps) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-foreground mb-2">
        {label}
      </label>
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
