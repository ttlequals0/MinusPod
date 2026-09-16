import { badgeBase, tint } from './badgeStyles';

/** Enabled state of a sponsor or pattern row. */
export function ActiveBadge({ active }: { active: boolean }) {
  return (
    <span className={`${badgeBase} ${active ? tint.success : tint.destructive}`}>
      {active ? 'Active' : 'Inactive'}
    </span>
  );
}
