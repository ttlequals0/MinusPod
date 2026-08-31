import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdReviewModal, { type AdReviewItem } from './AdReviewModal';

const mockReprocess = vi.fn();

vi.mock('wavesurfer.js', () => ({
  default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) },
}));
vi.mock('wavesurfer.js/dist/plugins/regions.esm.js', () => ({
  default: {
    create: vi.fn(() => ({
      addRegion: vi.fn(() => ({ setOptions: vi.fn() })),
    })),
  },
}));
vi.mock('./ad-editor/usePeaks', () => ({
  usePeaks: () => ({ peaks: [0.2, 0.5, 0.3], peakResolutionMs: 100, peaksError: null }),
}));
vi.mock('../api/sponsors', () => ({
  getSponsors: vi.fn().mockResolvedValue([]),
}));
vi.mock('../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/feeds')>()),
  reprocessEpisode: (...a: unknown[]) => mockReprocess(...a),
}));
vi.mock('./SplitMarkerModal', () => ({
  default: ({ target, onClose, onSplit }: {
    target: { start: number; end: number };
    onClose: () => void;
    onSplit: (r: { markerCount: number; patternIds: number[] }) => void;
  }) => (
    <div data-testid="split-modal" data-start={target.start} data-end={target.end}>
      <button onClick={() => onSplit({ markerCount: 2, patternIds: [1, 2] })}>
        finish split
      </button>
      <button onClick={onClose}>close split</button>
    </div>
  ),
}));

const ITEM: AdReviewItem = {
  podcastSlug: 'example-podcast',
  episodeId: 'a1b2c3d4e5f6',
  start: 100,
  end: 190,
  sponsor: 'Acme',
  reason: 'sponsor read',
  confidence: 0.9,
  detectionStage: 'first_pass',
  patternId: null,
  correctedBounds: null,
};

function renderModal(over: Partial<React.ComponentProps<typeof AdReviewModal>> = {}) {
  const onClose = vi.fn();
  const onSubmit = vi.fn();
  const onSkip = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AdReviewModal
        item={ITEM}
        episodeDuration={3600}
        onClose={onClose}
        onSubmit={onSubmit}
        onSkip={onSkip}
        {...over}
      />
    </QueryClientProvider>,
  );
  return { onClose, onSubmit, onSkip };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockReprocess.mockResolvedValue({});
});

describe('AdReviewModal split entry', () => {
  it('opens the split editor on the detected bounds', async () => {
    renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Split' }));
    const split = screen.getByTestId('split-modal');
    expect(split.getAttribute('data-start')).toBe('100');
    expect(split.getAttribute('data-end')).toBe('190');
  });

  it('hands a finished split to the host when it has a handler', async () => {
    const onSplitSaved = vi.fn();
    const { onClose } = renderModal({ onSplitSaved });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Split' }));
    await user.click(screen.getByRole('button', { name: 'finish split' }));
    expect(onSplitSaved).toHaveBeenCalledWith({ markerCount: 2, patternIds: [1, 2] });
    expect(screen.queryByTestId('split-modal')).toBeNull();
    expect(mockReprocess).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('recuts and closes itself when the host has no split handler', async () => {
    const { onClose } = renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Split' }));
    await user.click(screen.getByRole('button', { name: 'finish split' }));
    expect(mockReprocess).toHaveBeenCalledWith(
      'example-podcast', 'a1b2c3d4e5f6', 'recut');
    expect(onClose).toHaveBeenCalled();
  });
});

describe('AdReviewModal hideConfirm', () => {
  it('disables Save and mutes the C shortcut when confirm has nothing to do', async () => {
    const { onSubmit } = renderModal({ hideConfirm: true });
    const user = userEvent.setup();
    const save = screen.getByRole('button', { name: /Save/ });
    expect(save.hasAttribute('disabled')).toBe(true);
    expect(screen.getByText(
      'This ad is already cut. Move a boundary to save an adjustment.')).toBeTruthy();
    await user.keyboard('c');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('C confirms as-detected when confirm is available', async () => {
    const { onSubmit } = renderModal();
    const user = userEvent.setup();
    await user.keyboard('c');
    expect(onSubmit).toHaveBeenCalledWith({ kind: 'confirm', sponsor: 'Acme' });
  });
});

describe('AdReviewModal create mode category', () => {
  async function fillRequiredCreateFields(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText(/Sponsor name/), 'Morning Brew');
    await user.type(screen.getByLabelText(/Text template/), 'a'.repeat(60));
  }

  it('passes the chosen category to onCreate', async () => {
    const onCreate = vi.fn();
    renderModal({ mode: 'create', onCreate });
    const user = userEvent.setup();

    await fillRequiredCreateFields(user);
    await user.selectOptions(screen.getByLabelText(/Category/), 'cross_promo');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ category: 'cross_promo' }),
    );
  });

  it('sends null category when left as Uncategorized', async () => {
    const onCreate = vi.fn();
    renderModal({ mode: 'create', onCreate });
    const user = userEvent.setup();

    await fillRequiredCreateFields(user);
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ category: null }),
    );
  });
});

describe('AdReviewModal kept-by-category hotkeys', () => {
  it('mutes C and R on a keep marker; the buttons are hidden but keys are not', async () => {
    const { onSubmit } = renderModal({
      item: { ...ITEM, category: 'outro', actionApplied: 'keep' },
    });
    const user = userEvent.setup();
    await user.keyboard('r');
    await user.keyboard('c');
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Not an ad/ })).toBeNull();
  });
});
