import { useCallback, type HTMLAttributes, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { usePopover, type PopoverAlign, type PopoverCloseReason } from '../hooks/usePopover';

// Inset rings remain visible inside the scrolling panel.
const surface = 'max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-lg overflow-y-auto [&_:focus-visible]:ring-inset';

// style is the placement hook's to write, so it is not a caller's to pass.
interface PopoverProps extends Omit<HTMLAttributes<HTMLDivElement>, 'style'> {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: (reason: PopoverCloseReason) => void;
  align?: PopoverAlign;
  centerOnMobile?: boolean;
  children: ReactNode;
}

function PopoverPanel({
  anchorRef, onClose, align, centerOnMobile, className = '', children, ...rest
}: Omit<PopoverProps, 'open'>) {
  const { centered, panelProps } = usePopover({ onClose, anchorRef, align, centerOnMobile });
  const attachPanel = panelProps.ref;
  const attach = useCallback((node: HTMLDivElement | null) => {
    const owner = anchorRef.current?.closest<HTMLElement>('[role="dialog"]')?.dataset.dialogId;
    if (node && owner) node.dataset.modalOwner = owner;
    attachPanel(node);
  }, [anchorRef, attachPanel]);
  return createPortal(
    <div
      {...rest}
      {...panelProps}
      ref={attach}
      className={`fixed z-40 data-[modal-owner]:z-70 ${centered ? 'left-1/2 -translate-x-1/2' : ''} ${surface} ${className}`}
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
