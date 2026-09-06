import { btnSecondary } from './buttonStyles';
import { focusRing } from './fieldStyles';

/** First, last, and `neighbours` pages either side of the current one. */
function pageWindow(page: number, totalPages: number, neighbours: number) {
  const span = 3 + neighbours * 2;
  const out: (number | 'ellipsis')[] = [];
  if (totalPages <= span + 2) {
    for (let i = 1; i <= totalPages; i++) out.push(i);
    return out;
  }
  out.push(1);
  if (page > 2 + neighbours) out.push('ellipsis');
  const from = Math.max(2, page - neighbours);
  const to = Math.min(totalPages - 1, page + neighbours);
  for (let i = from; i <= to; i++) out.push(i);
  if (page < totalPages - 1 - neighbours) out.push('ellipsis');
  out.push(totalPages);
  return out;
}

function pageButtons(
  pages: (number | 'ellipsis')[], page: number,
  onPage: (p: number) => void, visibility: string,
) {
  return pages.map((p, i) => (p === 'ellipsis' ? (
    <span key={`e${i}`} className={`px-1 text-muted-foreground ${visibility}`}>...</span>
  ) : (
    <button
      key={p}
      onClick={() => onPage(p)}
      className={`px-2 sm:px-3 py-1.5 text-sm rounded transition-colors ${visibility} ${
        p === page ? 'bg-primary text-primary-foreground' : btnSecondary
      } ${focusRing}`}
    >
      {p}
    </button>
  )));
}
export function Pagination({
  page, totalPages, total, onPage,
}: {
  page: number;
  totalPages: number;
  total: number;
  onPage: (p: number) => void;
}) {
  if (totalPages <= 1) return null;
  const pages = pageWindow(page, totalPages, 1);
  // One neighbour each side is still four buttons plus Previous/Next, which
  // overflows a phone, so narrow screens drop to first/current/last.
  const narrowPages = pageWindow(page, totalPages, 0);
  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 mt-4 bg-card rounded-lg border border-border">
      <div className="text-sm text-muted-foreground">Page {page} of {totalPages} ({total} total)</div>
      <div className="flex items-center gap-1 sm:gap-2 justify-center">
        <button
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page === 1}
          className={`px-2 sm:px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          <span className="sm:hidden">Prev</span>
          <span className="hidden sm:inline">Previous</span>
        </button>
        {pageButtons(narrowPages, page, onPage, 'sm:hidden')}
        {pageButtons(pages, page, onPage, 'hidden sm:inline-flex')}
        <button
          onClick={() => onPage(Math.min(totalPages, page + 1))}
          disabled={page === totalPages}
          className={`px-2 sm:px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          Next
        </button>
      </div>
    </div>
  );
}
