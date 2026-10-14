import { ConfirmModal } from './Modal';

interface DeleteKeyRecordModalProps {
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function DeleteKeyRecordModal({ pending, onCancel, onConfirm }: DeleteKeyRecordModalProps) {
  return (
    <ConfirmModal
      title="Delete revoked subscriber key record?"
      confirmLabel="Delete"
      busyLabel="Deleting..."
      pending={pending}
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      <p>This only removes the revoked key record. The URL remains invalid.</p>
    </ConfirmModal>
  );
}

export default DeleteKeyRecordModal;
