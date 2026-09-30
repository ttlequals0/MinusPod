import { focusRing } from './fieldStyles';

interface SectionBulkControlsProps {
  // True while a search is active, since search already overrides expansion.
  disabled: boolean;
  onToggleAll: (open: boolean) => void;
}

export default function SectionBulkControls({ disabled, onToggleAll }: SectionBulkControlsProps) {
  const className = `text-sm text-primary hover:underline ${disabled ? 'opacity-50 pointer-events-none' : ''} ${focusRing}`;
  return (
    <div className="flex justify-end gap-3">
      <button type="button" onClick={() => onToggleAll(true)} disabled={disabled} className={className}>
        Expand all
      </button>
      <button type="button" onClick={() => onToggleAll(false)} disabled={disabled} className={className}>
        Collapse all
      </button>
    </div>
  );
}
