import { formatCost } from '../utils/format';

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
}

function unknownCostTitle(unpricedCount?: number, unit = 'call'): string {
  if (!unpricedCount) return UNKNOWN_COST_TITLE;
  const noun = unpricedCount === 1 ? unit : `${unit}s`;
  return `${UNKNOWN_COST_TITLE} (${unpricedCount} unpriced ${noun})`;
}

// Marks an amount as a known-spend floor. Same badge recipe as every other
// status chip: px-2 py-0.5 text-xs rounded on a 20% tint.
export function IncompleteBadge({ unpricedCount, unit }: { unpricedCount?: number; unit?: string }) {
  return (
    <span
      className="ml-1 px-2 py-0.5 text-xs rounded bg-warning/20 text-warning whitespace-nowrap"
      title={unknownCostTitle(unpricedCount, unit)}
    >
      Incomplete
    </span>
  );
}

// A cost with unpriced rows behind it never renders as a plain amount: a zero
// known total reads as an unavailable breakdown, anything else as the known
// part of a larger bill.
function CostAmount({ amount, unpricedCount, unpriced, unit }: CostAmountProps) {
  const incomplete = unpriced ?? (unpricedCount ?? 0) > 0;
  if (!incomplete) return <>{formatCost(amount)}</>;
  if (!amount) {
    return <span title={unknownCostTitle(unpricedCount, unit)}>Breakdown unavailable</span>;
  }
  return (
    <span>
      Known {formatCost(amount)}
      <IncompleteBadge unpricedCount={unpricedCount} unit={unit} />
    </span>
  );
}

export default CostAmount;
