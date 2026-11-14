import { dialogFooterBtn } from './Modal';
import { btnOutline, btnPrimary } from './buttonStyles';
import { DemoDisabled } from './DemoDisabled';

interface SaveCancelFooterProps {
  onCancel: () => void;
  onSave: () => void;
  pending: boolean;
  saveLabel: string;
}

export default function SaveCancelFooter({ onCancel, onSave, pending, saveLabel }: SaveCancelFooterProps) {
  return (
    <>
      <button
        onClick={onCancel}
        className={`${dialogFooterBtn} ${btnOutline}`}
      >
        Cancel
      </button>
      <DemoDisabled>
        <button
          onClick={onSave}
          disabled={pending}
          className={`${dialogFooterBtn} ${btnPrimary}`}
        >
          {pending ? 'Saving...' : saveLabel}
        </button>
      </DemoDisabled>
    </>
  );
}
