import { useEffect, useRef, useState } from 'react';

// Below Tailwind's sm breakpoint a popover is centered on the screen under the
// trigger's row: a row that wraps on a phone leaves no side with room.
const PHONE_MAX_WIDTH_PX = 640;

/** Phone-only fixed offset under the trigger, dismissed when the trigger moves. */
export function usePhonePlacement(open: boolean, onDismiss: () => void) {
  const [phoneTop, setPhoneTop] = useState<number | null>(null);

  // Keep the callback current without re-attaching the listeners on every render.
  const onDismissRef = useRef(onDismiss);
  useEffect(() => { onDismissRef.current = onDismiss; });

  // The offset is measured once as the popover opens, so anything that moves
  // the trigger has to dismiss it rather than leave it stranded. Only the
  // fixed phone placement strands; an anchored popover moves with its trigger.
  useEffect(() => {
    if (!open || phoneTop === null) return;
    const dismiss = () => onDismissRef.current();
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    return () => {
      window.removeEventListener('scroll', dismiss, true);
      window.removeEventListener('resize', dismiss);
    };
  }, [open, phoneTop]);

  // Call with the trigger's rect as the popover opens; null keeps the
  // default anchored placement.
  const placeFor = (rect: DOMRect | undefined) => {
    setPhoneTop(rect && window.innerWidth < PHONE_MAX_WIDTH_PX ? rect.bottom + 4 : null);
  };

  return { phoneTop, placeFor };
}
