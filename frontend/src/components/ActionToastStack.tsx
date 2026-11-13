import type { ReactNode } from 'react';
import CloseButton from './CloseButton';

interface ActionToastStackProps {
  error: string | null;
  onDismissError: () => void;
  children?: ReactNode;
}

// Bottom-right stack: dismissible error banner above any confirm modal.
export default function ActionToastStack({ error, onDismissError, children }: ActionToastStackProps) {
  return (
    <div className="fixed bottom-4 right-[max(1rem,env(safe-area-inset-right))] flex flex-col items-end gap-2">
      {error && (
        <div className="max-w-sm bg-destructive/10 border border-destructive text-destructive rounded-lg p-4 shadow-lg text-sm flex items-start gap-3">
          <span className="flex-1">{error}</span>
          <CloseButton onClick={onDismissError} label="Dismiss error" className="shrink-0" />
        </div>
      )}
      {children}
    </div>
  );
}
