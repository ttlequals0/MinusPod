import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdReviewModal, { type AdReviewItem } from './AdReviewModal';
import { formatTime } from '../utils/adReviewHelpers';

// Reproduces a drag-release/freeze race in the real modal that the panel mock
// misses: the first frozen span must retain its text before a second is selected.

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

// Run A: words 0..169 cover 0.0 - 60.0s, ~1030 chars once joined by spaces.
// Run B: words 170..179 cover 111.3 - 115.0s, well past A.end + 1s gap.
const { SEGMENTS } = vi.hoisted(() => {
  const words: { word: string; start: number; end: number }[] = [];
  for (let i = 0; i < 170; i++) {
    const start = Number((i * 0.3529).toFixed(4));
    words.push({ word: `alpha${i}`, start, end: Number((start + 0.3).toFixed(4)) });
  }
  for (let i = 0; i < 10; i++) {
    const start = Number((111.3 + i * 0.3).toFixed(4));
    words.push({ word: `bravo${i}`, start, end: Number((start + 0.25).toFixed(4)) });
  }
  return {
    SEGMENTS: [{
      start: 0,
      end: 140,
      text: words.map((w) => w.word).join(' '),
      words,
    }],
  };
});

vi.mock('../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/feeds')>()),
  getOriginalSegments: vi.fn().mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: SEGMENTS }),
  getTranscriptSpan: vi.fn().mockResolvedValue({ text: '' }),
}));

const ITEM: AdReviewItem = {
  podcastSlug: 'example-podcast',
  episodeId: 'a1b2c3d4e5f6',
  start: 0,
  end: 0,
  sponsor: null,
  reason: null,
  confidence: null,
  detectionStage: null,
  patternId: null,
  correctedBounds: null,
};

function renderModal() {
  const onCreate = vi.fn().mockResolvedValue(undefined);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <AdReviewModal
        item={ITEM}
        episodeDuration={140}
        mode="create"
        onCreate={onCreate}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return { onCreate, ...view };
}

// Points window.getSelection() at [startIdx, endIdx] without firing or
// waiting for anything, so callers control the exact mouseup/click timing.
function mockNativeSelection(container: HTMLElement, startIdx: number, endIdx: number) {
  const startEl = container.querySelector(`[data-widx="${startIdx}"]`) as HTMLElement;
  const endEl = container.querySelector(`[data-widx="${endIdx}"]`) as HTMLElement;
  const range = document.createRange();
  range.setStart(startEl, 0);
  range.setEnd(endEl, 0);
  vi.spyOn(window, 'getSelection').mockReturnValue({
    isCollapsed: false,
    rangeCount: 1,
    getRangeAt: () => range,
  } as unknown as Selection);
}

async function selectWords(container: HTMLElement, startIdx: number, endIdx: number) {
  const startEl = container.querySelector(`[data-widx="${startIdx}"]`) as HTMLElement;
  const endEl = container.querySelector(`[data-widx="${endIdx}"]`) as HTMLElement;
  const expectedStart = formatTime(Number(startEl.dataset.start));
  const expectedEnd = formatTime(Number(endEl.dataset.end));
  mockNativeSelection(container, startIdx, endIdx);
  fireEvent.mouseUp(container.querySelector('.select-text') as HTMLElement);
  await waitFor(() => {
    expect(screen.getByText(
      (content) => content.includes(expectedStart) && content.includes(expectedEnd),
    )).toBeTruthy();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AdReviewModal text-mode multi-span with the real TextSelectionPanel', () => {
  it('keeps run A\'s text when frozen right after the drag, before selecting run B', async () => {
    const { container } = renderModal();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'By text' }));
    await waitFor(() => {
      expect(container.querySelector('[data-widx="0"]')).toBeTruthy();
    });
    await user.type(screen.getByLabelText(/Sponsor name/), 'Acme');

    // Run A, frozen with no wait after the mouseup: commitSelection is
    // deferred one tick (setTimeout 0), so this freeze click races it.
    mockNativeSelection(container, 0, 169);
    fireEvent.mouseUp(container.querySelector('.select-text') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: 'Add another span' }));

    // Run B: far enough past A that it never merges.
    await selectWords(container, 170, 179);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    expect(screen.queryByText(/is only 0 characters/)).toBeNull();
    const save = await screen.findByRole('button', { name: 'Mark ad (2 spans)' });
    expect(save.hasAttribute('disabled')).toBe(false);
  });
});
