import { cloneElement } from 'react';
import type { ReactElement } from 'react';
import { IS_DEMO } from '../demo/flag';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';

type Disableable = ReactElement<{ disabled?: boolean; 'aria-disabled'?: boolean }>;

// Wrapper carries the tooltip because disabled buttons have pointer-events-none.
export function DemoDisabled({ children, when = true }: { children: Disableable; when?: boolean }) {
  if (!(IS_DEMO && when)) return children;
  return (
    <span className="inline-flex cursor-not-allowed" title={DEMO_WRITE_TOOLTIP}>
      {cloneElement(children, { disabled: true, 'aria-disabled': true })}
    </span>
  );
}

export function demoDisabledProps(): { disabled?: true; 'aria-disabled'?: true } {
  return IS_DEMO ? { disabled: true, 'aria-disabled': true } : {};
}
