import type { ReactNode } from 'react';
import { selectBase } from '../../components/fieldStyles';

export const rowLabel = 'text-muted-foreground whitespace-nowrap sm:w-32 shrink-0';
export const rowLabelTop = `${rowLabel} sm:pt-1.5`;

export const toTri = (v: boolean | null | undefined): 'on' | 'off' | null => (v == null ? null : v ? 'on' : 'off');
export const fromTri = (v: 'on' | 'off' | null): boolean | null => (v == null ? null : v === 'on');

export function GlobalOverrideRow({
  label, ariaLabel, value, globalOn, disabled, onChange, children,
}: {
  label: string;
  ariaLabel: string;
  value: 'on' | 'off' | null | undefined;
  globalOn: boolean;
  disabled: boolean;
  onChange: (value: 'on' | 'off' | null) => void;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2 sm:gap-3 text-sm">
      <span className={rowLabelTop}>{label}</span>
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        <select
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value === '' ? null : e.target.value as 'on' | 'off')}
          disabled={disabled}
          className={`self-start w-full sm:w-auto min-w-0 max-w-full ${selectBase}`}
          aria-label={ariaLabel}
        >
          <option value="">Use global ({globalOn ? 'on' : 'off'})</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
        <p className="text-xs text-muted-foreground">{children}</p>
      </div>
    </div>
  );
}
