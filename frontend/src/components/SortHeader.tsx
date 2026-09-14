import { useState } from 'react';
import { focusRing } from './fieldStyles';

export type SortDirection = 'asc' | 'desc';

// Sortable <th> shared by the Patterns, Sponsors, and History tables.
// className replaces the default px-4 padding, so pass e.g. 'px-2' to shrink
// it or 'px-4 hidden md:table-cell' to keep the padding and add visibility.
export function SortHeader<T extends string>({
  field,
  label,
  className = 'px-4',
  sortField,
  sortDirection,
  onSort,
}: {
  field: T;
  label: string;
  className?: string;
  sortField: T;
  sortDirection: SortDirection;
  onSort: (field: T) => void;
}) {
  const active = sortField === field;
  return (
    <th
      scope="col"
      aria-sort={active ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider hover:bg-accent/50 ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        className={`flex w-full items-center gap-1 py-1 -my-1 uppercase tracking-wider text-left ${focusRing}`}
      >
        {label}
        {active && (
          <span aria-hidden="true">{sortDirection === 'asc' ? '\u2191' : '\u2193'}</span>
        )}
      </button>
    </th>
  );
}

// Sort state plus the toggle those tables share: clicking the active column
// flips direction; a new column resets to initialDirection. onChange fires on
// every sort change (the pages use it to reset pagination).
export function useSortState<T extends string>(
  initialField: T,
  initialDirection: SortDirection,
  onChange?: () => void,
) {
  const [sortField, setSortField] = useState<T>(initialField);
  const [sortDirection, setSortDirection] = useState<SortDirection>(initialDirection);
  const handleSort = (field: T) => {
    if (sortField === field) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDirection(initialDirection);
    }
    onChange?.();
  };
  return { sortField, sortDirection, handleSort };
}
