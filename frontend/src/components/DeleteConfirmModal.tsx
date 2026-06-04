import { ReactNode } from 'react';

interface Props {
  title: string;
  children?: ReactNode;
  confirmLabel?: string;
  pending?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function DeleteConfirmModal({
  title, children, confirmLabel = 'Delete', pending = false, onCancel, onConfirm,
}: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
        <div className="p-4 border-b border-border">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        </div>
        <div className="p-4 space-y-3 text-sm text-foreground">{children}</div>
        <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm rounded border border-border hover:bg-accent transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={pending}
            className="px-3 py-1.5 text-sm rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50 transition-colors"
          >
            {pending ? 'Deleting...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default DeleteConfirmModal;
