import { useId } from 'react';
import { btnSecondary } from './buttonStyles';
import { focusRing, selectBase } from './fieldStyles';
import type { SortDirection } from './SortHeader';

export default function SortControls<T extends string>({ options, field, direction, onSort, className = '' }: {
  options: readonly { value: T; label: string }[];
  field: T;
  direction: SortDirection;
  onSort: (field: T) => void;
  className?: string;
}) {
  const id = useId();
  return <div className={`flex flex-wrap items-center gap-2 mb-4 ${className}`}>
    <label htmlFor={id} className="text-sm text-muted-foreground">Sort by</label>
    <select id={id} value={field} onChange={event => onSort(event.target.value as T)} className={`min-w-0 flex-1 ${selectBase}`}>
      {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    <button type="button" onClick={() => onSort(field)} className={`${btnSecondary} ${focusRing} rounded px-3 py-2 text-sm`} aria-label={`Sort ${direction === 'asc' ? 'descending' : 'ascending'}`}>
      {direction === 'asc' ? 'Ascending' : 'Descending'}
    </button>
  </div>;
}
