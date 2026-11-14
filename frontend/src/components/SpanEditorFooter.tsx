import { btnBase, btnOutline, btnPrimary } from './buttonStyles';
import { DemoDisabled } from './DemoDisabled';

/** Action bar of the single-span editor: Cancel and Save audio, with the save error beside them. */
export default function SpanEditorFooter({ error, saving, canSave, onCancel, onSave }: {
  error: string | null;
  saving: boolean;
  canSave: boolean;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <>
      <div className="min-w-0 text-xs text-muted-foreground">
        {error
          ? <span role="alert" className="text-destructive">{error}</span>
          : "Save audio rebuilds this pattern's audio from the selection."}
      </div>
      <div className="flex items-center gap-2">
        <button type="button" onClick={onCancel} disabled={saving} className={`${btnBase} ${btnOutline}`}>
          Cancel
        </button>
        <DemoDisabled>
          <button type="button" disabled={saving || !canSave} onClick={onSave} className={`${btnBase} ${btnPrimary}`}>
            {saving ? 'Saving audio...' : 'Save audio'}
          </button>
        </DemoDisabled>
      </div>
    </>
  );
}
