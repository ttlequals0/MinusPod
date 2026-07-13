import { useEffect, useRef, useState } from 'react';

interface ConfirmResetButtonProps {
  label: string;
  isPending: boolean;
  onConfirm: () => void;
}

// Two-click destructive reset (issue #513): the first click arms the button
// for 3s and asks for confirmation, the second fires onConfirm. Styled as an
// outlined destructive button so it reads as clickable, unlike the old
// secondary-background text.
function ConfirmResetButton({ label, isPending, onConfirm }: ConfirmResetButtonProps) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <button
      type="button"
      onClick={() => {
        if (armed) {
          if (timer.current) clearTimeout(timer.current);
          setArmed(false);
          onConfirm();
        } else {
          setArmed(true);
          timer.current = setTimeout(() => setArmed(false), 3000);
        }
      }}
      disabled={isPending}
      className={`px-4 py-2 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 ${
        armed
          ? 'border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/80'
          : 'border-destructive/40 text-destructive hover:bg-destructive/10'
      }`}
    >
      {isPending ? 'Resetting...' : armed ? 'Click again to confirm' : label}
    </button>
  );
}

export default ConfirmResetButton;
