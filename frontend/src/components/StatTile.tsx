import type { ReactNode } from 'react';
import { summaryCard, summaryValue } from './pageStyles';
import { focusRing } from './fieldStyles';

const TONE: Record<NonNullable<StatTileProps['tone']>, string> = {
  default: '',
  primary: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
};

interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: 'default' | 'primary' | 'success' | 'warning' | 'destructive';
  className?: string;
  onClick?: () => void;
  selected?: boolean;
  title?: string;
}

export default function StatTile({ label, value, hint, tone = 'default', className, onClick, selected, title }: StatTileProps) {
  const body = (
    <>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`${summaryValue} ${TONE[tone]}`}>{value}</div>
      {hint != null && <div className="text-xs text-muted-foreground">{hint}</div>}
    </>
  );
  const base = `${summaryCard} h-full ${className ?? ''}`;
  if (!onClick) return <div className={base}>{body}</div>;
  const state = selected ? 'border-primary bg-primary/5' : 'border-border';
  return (
    <button type="button" onClick={onClick} aria-pressed={selected ?? false} title={title}
      className={`${base} ${state} cursor-pointer transition-colors hover:border-primary/50 ${focusRing}`}>
      {body}
    </button>
  );
}
