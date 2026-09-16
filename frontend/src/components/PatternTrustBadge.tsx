import type { PatternTrust } from '../api/patterns';
import { badgeBase, tint } from './badgeStyles';

// Staleness-based trust tier for community patterns. 'active' renders
// nothing; absence of a badge is the default, unflagged state.
export function PatternTrustBadge({ trust }: { trust?: PatternTrust }) {
  if (!trust || trust === 'active') return null;
  if (trust === 'stale') {
    return (
      <span
        className={`${badgeBase} ${tint.warning}`}
        title="Not matched locally in over 90 days and not re-confirmed by the community in over a year"
      >
        Stale
      </span>
    );
  }
  return (
    <span
      className={`${badgeBase} ${tint.neutral}`}
      title="Not yet matched locally; trust builds up as it confirms ads on your feeds"
    >
      Unproven
    </span>
  );
}
