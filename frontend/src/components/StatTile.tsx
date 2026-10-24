import type { ReactNode } from 'react';
import { summaryCard } from './pageStyles';

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
}

export default function StatTile({ label, value, hint, tone = 'default', className }: StatTileProps) {
  return (
    <div className={`${summaryCard} h-full ${className ?? ''}`}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-xl font-bold tabular-nums ${TONE[tone]}`}>{value}</div>
      {hint != null && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}
