import type { PatternTrust } from '../api/patterns';

// Staleness-based trust tier for community patterns. 'active' renders
// nothing; absence of a badge is the default, unflagged state.
export function PatternTrustBadge({ trust }: { trust?: PatternTrust }) {
  if (!trust || trust === 'active') return null;
  if (trust === 'stale') {
    return (
      <span
        className="px-2 py-0.5 text-xs rounded bg-warning/20 text-warning"
        title="Not matched locally in over 90 days and not re-confirmed by the community in over a year"
      >
        Stale
      </span>
    );
  }
  return (
    <span
      className="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground"
      title="Not yet matched locally; trust builds up as it confirms ads on your feeds"
    >
      Unproven
    </span>
  );
}
