import { formatCost } from '../utils/format';
import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';

const UNKNOWN_COST_TITLE =
  'Some billable calls have no recorded price, so this amount is a floor, not the full spend';

interface CostAmountProps {
  amount: number;
  // Rows behind the amount that carry no price. Pass the count when there is
  // one, otherwise set unpriced to flag the gap without a number.
  unpricedCount?: number;
  unpriced?: boolean;
  // Noun for the unpriced rows, pluralized in the tooltip.
  unit?: string;
  // Turns the badge into a disclosure for the contributing calls. Without it
  // the badge stays a plain label.
  onInspect?: () => void;
  inspectExpanded?: boolean;
}

function unknownCostTitle(unpricedCount?: number, unit = 'call'): string {
  if (!unpricedCount) return UNKNOWN_COST_TITLE;
  const noun = unpricedCount === 1 ? unit : `${unit}s`;
  return `${UNKNOWN_COST_TITLE} (${unpricedCount} unpriced ${noun})`;
}

interface IncompleteBadgeProps {
  unpricedCount?: number;
  unit?: string;
  onInspect?: () => void;
  expanded?: boolean;
}

// Marks an amount as a known-spend floor, on the shared badge recipe.
// onInspect turns it into the entry point to the calls behind the gap.
export function IncompleteBadge({ unpricedCount, unit, onInspect, expanded }: IncompleteBadgeProps) {
  const title = unknownCostTitle(unpricedCount, unit);
  const className = `${badgeBase} ml-1 ${tint.warning} whitespace-nowrap`;
  if (!onInspect) return <span className={className} title={title}>Incomplete</span>;
  return (
    <button
      type="button"
      onClick={onInspect}
      aria-expanded={expanded}
      title={`${title}. Show the contributing calls.`}
      className={`${className} underline decoration-dotted underline-offset-2 ${focusRing}`}
    >
      Incomplete
    </button>
  );
}

// A cost with unpriced rows behind it never renders as a plain amount: a zero
// known total reads as an unavailable breakdown, anything else as the known
// part of a larger bill.
function CostAmount({
  amount, unpricedCount, unpriced, unit, onInspect, inspectExpanded,
}: CostAmountProps) {
  const incomplete = unpriced ?? (unpricedCount ?? 0) > 0;
  if (!incomplete) return <>{formatCost(amount)}</>;
  const badge = (
    <IncompleteBadge
      unpricedCount={unpricedCount}
      unit={unit}
      onInspect={onInspect}
      expanded={inspectExpanded}
    />
  );
  if (!amount) {
    return (
      <span>
        <span title={unknownCostTitle(unpricedCount, unit)}>Breakdown unavailable</span>
        {onInspect && badge}
      </span>
    );
  }
  return (
    <span>
      Known {formatCost(amount)}
      {badge}
    </span>
  );
}

export default CostAmount;
