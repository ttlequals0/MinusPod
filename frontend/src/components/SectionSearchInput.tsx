import { Search, X } from 'lucide-react';
import { focusRing } from './fieldStyles';

interface SectionSearchInputProps {
  value: string;
  onChange: (value: string) => void;
  onClear: () => void;
  placeholder: string;
  ariaLabel: string;
  clearLabel: string;
}

export default function SectionSearchInput({
  value,
  onChange,
  onClear,
  placeholder,
  ariaLabel,
  clearLabel,
}: SectionSearchInputProps) {
  return (
    <div className="relative">
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        className="w-full rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground pl-9 pr-9 py-2 focus:outline-hidden focus:ring-2 focus:ring-ring"
      />
      {value && (
        <button
          type="button"
          onClick={onClear}
          aria-label={clearLabel}
          className={`absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-muted-foreground hover:text-foreground touch-manipulation ${focusRing}`}
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
