/**
 * Loading placeholders that hold the shape of the content they stand in for,
 * so a page does not jump when data arrives. Per the design guide: skeletons
 * are animate-pulse rows in the muted tone, never a spinner in a blank page.
 */

export function Skeleton({ className = '' }: { className?: string }) {
  return <div aria-hidden className={`bg-muted rounded animate-pulse ${className}`} />;
}

/** A row of stat cards: label line over a value line, matching StatCard. */
export function SkeletonStatCards({ count, className = '' }: { count: number; className?: string }) {
  return (
    <div className={className} data-testid="skeleton-stat-cards">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="bg-card rounded-lg border border-border p-4 space-y-3">
          <Skeleton className="h-3.5 w-2/3" />
          <Skeleton className="h-6 w-1/2" />
        </div>
      ))}
    </div>
  );
}

/** A chart panel: heading line over a plot-height block. */
export function SkeletonChart({ height = 300 }: { height?: number }) {
  return (
    <div className="bg-card rounded-lg border border-border p-4" data-testid="skeleton-chart">
      <Skeleton className="h-5 w-56 mb-4" />
      <div aria-hidden className="w-full bg-muted rounded animate-pulse" style={{ height }} />
    </div>
  );
}

/** Stacked list rows: a title line and a shorter detail line each. */
export function SkeletonRows({ count, className = '' }: { count: number; className?: string }) {
  return (
    <div className={`space-y-3 ${className}`} data-testid="skeleton-rows">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** A page header: title line with a control slot on the right. */
export function SkeletonPageHeader() {
  return (
    <div className="flex items-center justify-between gap-4 mb-6" data-testid="skeleton-page-header">
      <Skeleton className="h-8 w-40" />
      <Skeleton className="h-9 w-32" />
    </div>
  );
}
