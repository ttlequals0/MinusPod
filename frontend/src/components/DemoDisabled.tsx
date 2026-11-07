import { cloneElement } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { IS_DEMO } from '../demo/flag';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';

type Disableable = ReactElement<{ disabled?: boolean; 'aria-disabled'?: boolean; title?: string; className?: string; children?: ReactNode }>;

// Wrapper carries the tooltip because disabled buttons have pointer-events-none.
export function DemoDisabled({ children, when = true, title = DEMO_WRITE_TOOLTIP }: { children: Disableable; when?: boolean; title?: string }) {
  if (!(IS_DEMO && when)) return children;
  // An anchor would still navigate, so swap it for a disabled button with the same look.
  const control = children.type === 'a'
    ? <button type="button" className={children.props.className} disabled aria-disabled>{children.props.children}</button>
    : cloneElement(children, { disabled: true, 'aria-disabled': true, title: undefined });
  return (
    <span className="inline-flex cursor-not-allowed" title={title}>
      {control}
    </span>
  );
}
