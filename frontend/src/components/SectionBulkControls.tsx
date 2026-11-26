import { btnBase, btnSecondary } from './buttonStyles';

interface SectionBulkControlsProps {
  // True while a search is active, since search already overrides expansion.
  disabled: boolean;
  onToggleAll: (open: boolean) => void;
  matchCount?: number | null;
}

export default function SectionBulkControls({ disabled, onToggleAll, matchCount }: SectionBulkControlsProps) {
  const className = `${btnBase} ${btnSecondary} text-xs px-2.5 py-1`;
  return (
    <div className="flex items-center justify-end gap-2">
      {matchCount != null && matchCount > 0 && (
        <span className="text-xs text-muted-foreground mr-auto">
          {matchCount} matching {matchCount === 1 ? 'section' : 'sections'}
        </span>
      )}
      <button type="button" onClick={() => onToggleAll(true)} disabled={disabled} className={className}>
        Expand all
      </button>
      <button type="button" onClick={() => onToggleAll(false)} disabled={disabled} className={className}>
        Collapse all
      </button>
    </div>
  );
}
