import { autoClosedNote, holdReasonTitle } from '../utils/holdReason';

interface AutoClosedNoteProps {
  reason?: string | null;
  className?: string;
  testId?: string;
}

// Why a detection was closed automatically instead of held for review.
export function AutoClosedNote({ reason, className, testId = 'auto-closed-note' }: AutoClosedNoteProps) {
  return (
    <span className={className} title={holdReasonTitle(reason)} data-testid={testId}>
      {autoClosedNote(reason)}
    </span>
  );
}
