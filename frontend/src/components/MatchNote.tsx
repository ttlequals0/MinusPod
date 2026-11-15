import type { ReactNode } from 'react';
import { Link } from 'react-router';
import type { AdSegment } from '../api/types';
import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';

export function LearnedChip() {
  return (
    <span
      className={`${badgeBase} ${tint.purple}`}
      title="This show's own ask or promo, learned from a read you confirmed"
    >
      Learned
    </span>
  );
}

/** A learned show pattern's note; any other marker keeps the caller's own rendering. */
export default function MatchNote({ segment, fallback }: { segment: AdSegment; fallback: ReactNode }) {
  const learned = segment.patternSummary;
  if (!learned) return <>{fallback}</>;
  const times = (n: number) => (n === 1 ? 'once' : `${n} times`);
  const tally = learned.confirmedCount > 0
    ? `confirmed by you ${times(learned.confirmedCount)}`
    : `matched ${times(learned.matchCount)}`;
  return (
    <Link
      to={`/patterns?id=${learned.id}`}
      className={`text-primary hover:underline ${focusRing}`}
      title="Open this pattern"
      onClick={(e) => e.stopPropagation()}
    >
      Learned from this show: {learned.templateStart} ({tally})
    </Link>
  );
}
