import { cloneElement } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { IS_DEMO } from '../demo/flag';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';

type Disableable = ReactElement<{ disabled?: boolean; 'aria-disabled'?: boolean; title?: string; className?: string; children?: ReactNode }>;

// Variant prefixes include arbitrary ones such as max-[359px]:.
const SIZING_TOKEN = /^(?:[^\s:]+:)*(?:flex-1|grow(?:-\d+)?|shrink(?:-\d+)?|basis-\S+|w-\S+)$/;
const WIDTH_OR_FLEX_TOKEN = /^(?:[^\s:]+:)*(?:flex-1|grow(?:-\d+)?|basis-\S+|w-\S+)$/;

// Wrapper carries the tooltip because disabled buttons have pointer-events-none.
export function DemoDisabled({ children, when = true, title = DEMO_WRITE_TOOLTIP }: { children: Disableable; when?: boolean; title?: string }) {
  if (!(IS_DEMO && when)) return children;
  // An anchor would still navigate, so swap it for a disabled button with the same look.
  const tokens = (children.props.className ?? '').split(/\s+/).filter(Boolean);
  const moved = tokens.filter(t => SIZING_TOKEN.test(t));
  const fill = moved.some(t => WIDTH_OR_FLEX_TOKEN.test(t));
  const kept = tokens.filter(t => !SIZING_TOKEN.test(t));
  if (fill) kept.push('w-full');
  const childClass = moved.length ? kept.join(' ') : children.props.className;
  const control = children.type === 'a'
    ? <button type="button" className={childClass} disabled aria-disabled>{children.props.children}</button>
    : cloneElement(children, { disabled: true, 'aria-disabled': true, title: undefined, className: childClass });
  return (
    <span className={['inline-flex cursor-not-allowed', ...moved].join(' ')} title={title}>
      {control}
    </span>
  );
}
