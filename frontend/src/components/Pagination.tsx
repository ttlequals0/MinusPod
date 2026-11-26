import type { ReactNode } from 'react';
import { btnBase, btnPrimary, btnSecondary } from './buttonStyles';

/** First, last, and `neighbours` pages either side of the current one. */
export function pageWindow(page: number, totalPages: number, neighbours: number) {
  const span = 3 + neighbours * 2;
  const out: (number | 'ellipsis')[] = [];
  if (totalPages <= span + (neighbours ? 2 : 0)) {
    for (let i = 1; i <= totalPages; i++) out.push(i);
    return out;
  }
  out.push(1);
  const from = Math.max(2, page - neighbours);
  const to = Math.min(totalPages - 1, page + neighbours);
  // An ellipsis never stands in for a single page: show that page instead.
  if (from === 3) out.push(2);
  else if (from > 3) out.push('ellipsis');
  for (let i = from; i <= to; i++) out.push(i);
  if (to === totalPages - 2) out.push(totalPages - 1);
  else if (to < totalPages - 2) out.push('ellipsis');
  out.push(totalPages);
  return out;
}

function pageButtons(
  pages: (number | 'ellipsis')[], page: number,
  onPage: (p: number) => void, visibility: string,
) {
  return pages.map((p, i) => (p === 'ellipsis' ? (
    <span key={`e${i}`} aria-hidden="true" className={`px-1 text-muted-foreground ${visibility}`}>...</span>
  ) : (
    <button
      key={p}
      onClick={() => onPage(p)}
      aria-label={`Page ${p}`}
      aria-current={p === page ? 'page' : undefined}
      className={`${btnBase} ${visibility} ${p === page ? btnPrimary : btnSecondary}`}
    >
      {p}
    </button>
  )));
}
export function Pagination({
  page, totalPages, total, onPage, footer,
}: {
  page: number;
  totalPages: number;
  total: number;
  onPage: (p: number) => void;
  footer?: ReactNode;
}) {
  if (totalPages <= 1) return null;
  const pages = pageWindow(page, totalPages, 1);
  // One neighbour each side is still four buttons plus Previous/Next, which
  // overflows a phone, so narrow screens drop to first/current/last.
  const narrowPages = pageWindow(page, totalPages, 0);
  const info = <div className="text-sm text-muted-foreground">Page {page} of {totalPages} ({total} total)</div>;
  const controls = (
    <div className="flex flex-wrap items-center gap-1 sm:gap-2 justify-center">
      <button
        onClick={() => onPage(Math.max(1, page - 1))}
        disabled={page === 1}
        className={`${btnBase} ${btnSecondary}`}
      >
        <span className="sm:hidden">Prev</span>
        <span className="hidden sm:inline">Previous</span>
      </button>
      {pageButtons(narrowPages, page, onPage, 'sm:hidden')}
      {pageButtons(pages, page, onPage, 'max-sm:hidden')}
      <button
        onClick={() => onPage(Math.min(totalPages, page + 1))}
        disabled={page === totalPages}
        className={`${btnBase} ${btnSecondary}`}
      >
        Next
      </button>
    </div>
  );
  // With a footer the card reads: info and footer on one wrapping header row, a rule, then the page buttons.
  return (
    <div className={`px-4 py-3 mt-4 bg-card rounded-lg border border-border ${footer ? 'space-y-2.5' : ''}`}>
      {footer ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            {info}
            {footer}
          </div>
          <div className="pt-2.5 border-t border-border/60">{controls}</div>
        </>
      ) : (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
          {info}
          {controls}
        </div>
      )}
    </div>
  );
}
