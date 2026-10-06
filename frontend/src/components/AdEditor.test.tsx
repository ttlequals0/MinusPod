import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import AdEditor from './AdEditor';
import type { AdCreateSubmit } from './AdReviewModal';

// AdReviewModal's own UI (waveform, transcript panel) is out of scope here;
// this double just exercises AdEditor's create-submit routing, same
// boundary AdReviewModal.test.tsx draws around its own children.
vi.mock('./AdReviewModal', () => ({
  default: ({ onCreate, onCreateDone }: {
    onCreate: (s: AdCreateSubmit, meta?: { silent?: boolean }) => Promise<void> | void;
    onCreateDone?: () => void;
  }) => (
    <div>
      <button
        type="button"
        onClick={() => {
          onCreate({
            kind: 'create', start: 1, end: 2, sponsor: 'Acme',
            textTemplate: 'a'.repeat(60), scope: 'podcast', reason: '', category: null,
          });
        }}
      >
        submit single run
      </button>
      <button
        type="button"
        onClick={async () => {
          await onCreate({
            kind: 'create', start: 10, end: 20, sponsor: 'Acme',
            textTemplate: 'a'.repeat(60), scope: 'podcast', reason: '', category: null,
          }, { silent: true });
          await onCreate({
            kind: 'create', start: 30, end: 40, sponsor: 'Acme',
            textTemplate: 'b'.repeat(60), scope: 'podcast', reason: '', category: null,
          }, { silent: true });
          onCreateDone?.();
        }}
      >
        submit two runs
      </button>
    </div>
  ),
}));

function renderEditor() {
  const onCorrection = vi.fn();
  const onCorrectionAsync = vi.fn().mockResolvedValue(undefined);
  const view = render(
    <MemoryRouter>
      <AdEditor
        detectedAds={[]}
        audioDuration={100}
        onCorrection={onCorrection}
        onCorrectionAsync={onCorrectionAsync}
        createMode
      />
    </MemoryRouter>,
  );
  return { onCorrection, onCorrectionAsync, ...view };
}

describe('AdEditor create submit path', () => {
  it('a single-run submit calls onCorrection and never onCorrectionAsync', async () => {
    const { onCorrection, onCorrectionAsync } = renderEditor();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'submit single run' }));

    expect(onCorrection).toHaveBeenCalledTimes(1);
    expect(onCorrection).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'create', start: 1, end: 2 }),
    );
    expect(onCorrectionAsync).not.toHaveBeenCalled();
  });

  it('a two-run submit calls onCorrectionAsync twice in time order', async () => {
    const { onCorrection, onCorrectionAsync } = renderEditor();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'submit two runs' }));

    expect(onCorrectionAsync).toHaveBeenCalledTimes(2);
    expect(onCorrectionAsync.mock.calls[0][0]).toEqual(
      expect.objectContaining({ start: 10, end: 20 }),
    );
    expect(onCorrectionAsync.mock.calls[1][0]).toEqual(
      expect.objectContaining({ start: 30, end: 40 }),
    );
    expect(onCorrection).not.toHaveBeenCalled();
  });
});
