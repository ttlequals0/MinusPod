import type { ReactNode } from 'react';

// A tab panel's card: the tab already names the section, so the heading is screen-reader only.
export default function TabPanelCard({ title, subtitle, children }: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="bg-card rounded-lg border border-border p-4 sm:p-6">
      <h2 className="sr-only">{title}</h2>
      {subtitle && <p className="text-sm text-muted-foreground mb-4">{subtitle}</p>}
      {children}
    </div>
  );
}
