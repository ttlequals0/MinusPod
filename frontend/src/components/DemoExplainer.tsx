import type { ReactNode } from 'react';
import { IS_DEMO } from '../demo/flag';
import { cardPad, cardTitle } from './pageStyles';

export function DemoExplainer({ title, children }: { title: string; children: ReactNode }) {
  if (!IS_DEMO) return null;
  return (
    <div className={`rounded-lg border bg-card ${cardPad}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Not available in the demo</p>
      <h3 className={cardTitle}>{title}</h3>
      <div className="mt-1 text-sm text-muted-foreground">{children}</div>
    </div>
  );
}
