import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Link } from 'react-router-dom';

// One prev/next pill, shared by the episode and feed detail headers. `side`
// fixes the chevron position (prev = left, next = right); `label` is the visible
// text. A null `to` (list boundary) renders disabled. `title` is the hover/aria
// text. Kept presentational on purpose -- each page computes its own neighbors.
export default function PrevNextLink({ to, side, label, title }: {
  to: string | null;
  side: 'prev' | 'next';
  label: string;
  title: string;
}) {
  const icon = side === 'prev'
    ? <ChevronLeft className="w-4 h-4" />
    : <ChevronRight className="w-4 h-4" />;
  const base = 'inline-flex items-center gap-1 px-2 py-1 rounded-md text-sm border transition-colors';

  if (!to) {
    return (
      <span
        className={`${base} border-border text-muted-foreground/40 cursor-not-allowed`}
        aria-disabled="true"
        title={title}
      >
        {side === 'prev' && icon}
        <span className="hidden sm:inline">{label}</span>
        {side === 'next' && icon}
      </span>
    );
  }
  return (
    <Link
      to={to}
      className={`${base} border-border bg-card text-foreground hover:bg-accent hover:text-accent-foreground hover:border-foreground/30`}
      title={title}
      aria-label={title}
    >
      {side === 'prev' && icon}
      <span className="hidden sm:inline">{label}</span>
      {side === 'next' && icon}
    </Link>
  );
}
