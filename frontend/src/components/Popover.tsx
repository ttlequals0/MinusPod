import { type HTMLAttributes, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { usePopover, type PopoverAlign, type PopoverCloseReason } from '../hooks/usePopover';

// The panel scrolls itself once the placement caps its height, so rings inside
// are drawn inset: an outset ring is clipped by that scroll box. z-40 clears the
// sticky header (z-30) and stays under the fixed z-50 bars and modals, which the
// placement already keeps it clear of.
const surface = 'max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-lg overflow-y-auto z-40 [&_:focus-visible]:ring-inset';

// style is the placement hook's to write, so it is not a caller's to pass.
interface PopoverProps extends Omit<HTMLAttributes<HTMLDivElement>, 'style'> {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: (reason: PopoverCloseReason) => void;
  align?: PopoverAlign;
  children: ReactNode;
}

function PopoverPanel({
  anchorRef, onClose, align, className = '', children, ...rest
}: Omit<PopoverProps, 'open'>) {
  const { centered, panelProps } = usePopover({ onClose, anchorRef, align });
  return createPortal(
    <div
      {...rest}
      {...panelProps}
      className={`fixed ${centered ? 'left-1/2 -translate-x-1/2' : ''} ${surface} ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}

/** Portals a panel to the body, placed against its anchor and dismissed with it.
 *  The hooks mount with the panel, so build the children behind the same `open`. */
function Popover({ open, ...props }: PopoverProps) {
  return open ? <PopoverPanel {...props} /> : null;
}

export default Popover;
