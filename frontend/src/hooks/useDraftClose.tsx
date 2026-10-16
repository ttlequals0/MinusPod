import { useState } from 'react';
import { ConfirmModal } from '../components/Modal';
import { useUnsavedChanges } from './useUnsavedChanges';

export function useDraftClose(dirty: boolean, onClose: () => void, pending = false) {
  const [confirming, setConfirming] = useState(false);
  useUnsavedChanges(dirty);
  return {
    requestClose: () => {
      if (pending) return;
      if (dirty) setConfirming(true);
      else onClose();
    },
    confirmation: confirming && <ConfirmModal title="Discard unsaved changes?" confirmLabel="Discard changes"
      pending={pending} onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); onClose(); }}>
      Your edits have not been saved.
    </ConfirmModal>,
  };
}
