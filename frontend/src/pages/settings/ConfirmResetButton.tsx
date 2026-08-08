import { useEffect, useRef, useState } from 'react';

interface ConfirmResetButtonProps {
  label: string;
  isPending?: boolean;
  onConfirm: () => void;
  // 'compact' fits beside a field label (issue #626); 'default' is the
  // toolbar-level sizing used by the section-wide reset buttons.
  size?: 'default' | 'compact';
  // Disables without changing to the pending label (issue #626 follow-up):
  // used when there's nothing to reset, e.g. a prompt already at default.
  disabled?: boolean;
  title?: string;
}

// Two-click destructive reset (issue #513): the first click arms the button
// for 3s and asks for confirmation, the second fires onConfirm. Styled as an
// outlined destructive button so it reads as clickable, unlike the old
// secondary-background text.
function ConfirmResetButton({
  label, isPending = false, onConfirm, size = 'default', disabled = false, title,
}: ConfirmResetButtonProps) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <button
      type="button"
      title={title}
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
      disabled={isPending || disabled}
      className={`rounded-lg border font-medium transition-colors disabled:opacity-50 ${
        size === 'compact' ? 'px-2 py-1 text-xs' : 'px-4 py-2 text-sm'
      } ${
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
