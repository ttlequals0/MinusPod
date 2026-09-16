import { badgeBase, tint } from './badgeStyles';

export type StatusDotTone = 'success' | 'warning' | 'neutral';

// The dot is a solid fill of the chip's own hue, so it is not a tint entry.
const DOT: Record<StatusDotTone, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  neutral: 'bg-muted-foreground/60',
};

/** Settings status chip: a filled dot in the tone's hue, then its label. */
export function StatusDotBadge({ tone, label }: { tone: StatusDotTone; label: string }) {
  return (
    <span className={`${badgeBase} inline-flex items-center gap-1.5 font-medium ${tint[tone]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${DOT[tone]}`} />
      {label}
    </span>
  );
}
