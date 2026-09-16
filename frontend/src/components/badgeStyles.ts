// The design guide's one badge shape; tint and weight stay at call sites.
export const badgeBase = 'px-2 py-0.5 text-xs rounded';

// Each hue pairs with its on-tint text token, solved for the worst backdrop a
// badge meets: a 30 percent hover fill inside a 10 percent panel of that hue.
export const tint = {
  // muted and secondary differ in 19 of the 40 theme/mode pairs, so the neutral
  // fill keeps its own token; muted-foreground is too faint on it to pass AA.
  neutral: 'bg-muted text-secondary-foreground',
  secondary: 'bg-secondary text-secondary-foreground',
  primary: 'bg-primary/20 text-primary-on-tint',
  blue: 'bg-c-blue/20 text-c-blue-on-tint',
  purple: 'bg-c-purple/20 text-c-purple-on-tint',
  teal: 'bg-c-teal/20 text-c-teal-on-tint',
  success: 'bg-success/20 text-success-on-tint',
  warning: 'bg-warning/20 text-warning-on-tint',
  destructive: 'bg-destructive/20 text-destructive-on-tint',
} as const;
