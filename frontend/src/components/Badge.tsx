import type { MouseEventHandler, ReactNode } from 'react';
import { badgeBase, tint } from './badgeStyles';

interface Props {
  tone: keyof typeof tint;
  title?: string;
  className?: string;
  // Let a long name wrap instead of ending in an ellipsis.
  wrap?: boolean;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  children: ReactNode;
}

// badgeBase that stays inside its container: long text ends in an ellipsis, so pass the full text as title.
export function Badge({ tone, title, className, wrap = false, onClick, children }: Props) {
  const classes = [badgeBase, 'max-w-full', tint[tone], wrap && 'whitespace-normal! break-words', className].filter(Boolean).join(' ');
  const content = wrap ? children : <span className="truncate">{children}</span>;
  if (onClick) return <button type="button" onClick={onClick} className={classes} title={title}>{content}</button>;
  return <span className={classes} title={title}>{content}</span>;
}
