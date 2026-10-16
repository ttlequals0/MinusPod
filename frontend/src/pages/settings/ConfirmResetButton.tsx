import { useState } from 'react';
import { ConfirmModal } from '../../components/Modal';
import { focusRing } from '../../components/fieldStyles';

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
  // Consequence shown as visible text while armed, for touch users who never
  // see a title tooltip.
  confirmHint?: string;
  // Accessible name, for pages carrying more than one "Reset" button.
  ariaLabel?: string;
  className?: string;
}

function ConfirmResetButton({
  label, isPending = false, onConfirm, size = 'default', disabled = false, title,
  confirmHint, ariaLabel, className = '',
}: ConfirmResetButtonProps) {
  const [armed, setArmed] = useState(false);
  const button = (
    <button
      type="button"
      title={title}
      aria-label={ariaLabel}
      onClick={() => setArmed(true)}
      disabled={isPending || disabled}
      className={`rounded-lg border font-medium transition-colors disabled:opacity-50 ${
        size === 'compact' ? 'px-2 py-1 text-xs' : 'px-4 py-2 text-sm'
      } ${
        armed
          ? 'border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/80'
          : 'border-destructive/40 text-destructive hover:bg-destructive/10'
      } ${focusRing} ${className}`}
    >
      {isPending ? 'Resetting...' : label}
    </button>
  );

  return (
    <>
      {button}
      {armed && <ConfirmModal title={`${ariaLabel || label}?`} confirmLabel={label} busyLabel="Resetting..." pending={isPending}
        onCancel={() => setArmed(false)} onConfirm={() => { setArmed(false); onConfirm(); }}>
        {confirmHint || 'Restore the default value. This replaces your current setting.'}
      </ConfirmModal>}
    </>
  );
}

export default ConfirmResetButton;
