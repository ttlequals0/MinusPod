import { useRef, useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TextSelectionPanel, { type TextRun } from './TextSelectionPanel';

// vi.mock factories are hoisted above the file's top-level consts, so the
// fixture lives inside vi.hoisted to avoid a use-before-initialization error.
const { SEGMENTS } = vi.hoisted(() => {
  const WORDS = [
    { word: 'alpha', start: 0.0, end: 0.9 },
    { word: 'bravo', start: 1.0, end: 1.9 },
    { word: 'charlie', start: 2.0, end: 2.9 },
    // Gap from run A's end (2.9) is 0.3s -- merges with A on freeze.
    { word: 'delta', start: 3.2, end: 4.1 },
    { word: 'echo', start: 4.2, end: 5.1 },
    { word: 'foxtrot', start: 5.2, end: 6.1 },
    // Gap from run B's end (6.1) is 3.9s -- never merges.
    { word: 'golf', start: 10.0, end: 10.9 },
    { word: 'hotel', start: 11.0, end: 11.9 },
    { word: 'india', start: 12.0, end: 12.9 },
  ];
  return {
    SEGMENTS: [{
      start: 0,
      end: 13,
      text: WORDS.map((w) => w.word).join(' '),
      words: WORDS,
    }],
  };
});

vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  getOriginalSegments: vi.fn().mockResolvedValue({ episodeId: 'a1b2c3d4e5f6', segments: SEGMENTS }),
}));

// Mirrors how AdReviewModal owns adStart/adEnd and bounces them back through
// props, so commitSelection's round trip through the real parent is exercised
// rather than assumed.
function Harness({
  onRunsChange,
  onSelectionChange,
}: {
  onRunsChange: (runs: TextRun[]) => void;
  onSelectionChange: (start: number, end: number, text: string) => void;
}) {
  const [adStart, setAdStart] = useState(0);
  const [adEnd, setAdEnd] = useState(0);
  const [playbackRate, setPlaybackRate] = useState(1);
  const audioRef = useRef<HTMLAudioElement>(null);
  return (
    <TextSelectionPanel
      slug="example-podcast"
      episodeId="a1b2c3d4e5f6"
      episodeDuration={20}
      audioRef={audioRef}
      adStart={adStart}
      adEnd={adEnd}
      onSelectionChange={(start, end, text) => {
        setAdStart(start);
        setAdEnd(end);
        onSelectionChange(start, end, text);
      }}
      onRunsChange={onRunsChange}
      playbackRate={playbackRate}
      setPlaybackRate={setPlaybackRate}
    />
  );
}

function renderPanel() {
  const onRunsChange = vi.fn();
  const onSelectionChange = vi.fn();
  const view = render(
    <Harness onRunsChange={onRunsChange} onSelectionChange={onSelectionChange} />,
  );
  return { onRunsChange, onSelectionChange, ...view };
}

async function waitForTranscript(container: HTMLElement) {
  await waitFor(() => {
    expect(container.querySelector('[data-widx="0"]')).toBeTruthy();
  });
}

// Simulates dragging a selection across words [startIdx, endIdx] and the
// mouseup that commits it, through the component's real resolveSelection
// (word-snap via [data-widx]) rather than calling an internal helper.
async function selectWords(container: HTMLElement, startIdx: number, endIdx: number) {
  const root = container.querySelector('.select-text') as HTMLElement;
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
  fireEvent.mouseUp(root);
  // commitSelection runs on a deferred tick (setTimeout 0) after mouseup.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TextSelectionPanel run list', () => {
  it('adds a run: freezing the current selection reports it and clears the selection', async () => {
    const { container, onRunsChange, onSelectionChange } = renderPanel();
    await waitForTranscript(container);

    await selectWords(container, 0, 2);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Add another span' }));

    expect(onRunsChange).toHaveBeenLastCalledWith([
      { start: 0, end: 2.9, text: 'alpha bravo charlie' },
    ]);
    expect(screen.getByText('0:00.0 - 0:02.9')).toBeTruthy();
    expect(onSelectionChange).toHaveBeenLastCalledWith(0, 0, '');
    expect(screen.getByRole('button', { name: 'Add another span' }).hasAttribute('disabled'))
      .toBe(true);
  });

  it('removes a run and reports the remaining list', async () => {
    const { container, onRunsChange } = renderPanel();
    await waitForTranscript(container);
    const user = userEvent.setup();

    await selectWords(container, 0, 2);
    await user.click(screen.getByRole('button', { name: 'Add another span' }));
    await selectWords(container, 6, 8);
    await user.click(screen.getByRole('button', { name: 'Add another span' }));

    expect(onRunsChange).toHaveBeenLastCalledWith([
      { start: 0, end: 2.9, text: 'alpha bravo charlie' },
      { start: 10, end: 12.9, text: 'golf hotel india' },
    ]);

    await user.click(screen.getByRole('button', { name: 'Remove span 0:00.0 to 0:02.9' }));

    expect(onRunsChange).toHaveBeenLastCalledWith([
      { start: 10, end: 12.9, text: 'golf hotel india' },
    ]);
    expect(screen.queryByText('0:00.0 - 0:02.9')).toBeNull();
    expect(screen.getByText('0:10.0 - 0:12.9')).toBeTruthy();
  });

  it('merges runs that sit within 1s of each other on freeze', async () => {
    const { container, onRunsChange } = renderPanel();
    await waitForTranscript(container);
    const user = userEvent.setup();

    await selectWords(container, 0, 2); // run A: 0 - 2.9
    await user.click(screen.getByRole('button', { name: 'Add another span' }));
    await selectWords(container, 3, 5); // run B: 3.2 - 6.1, 0.3s gap from A
    await user.click(screen.getByRole('button', { name: 'Add another span' }));

    expect(onRunsChange).toHaveBeenLastCalledWith([
      { start: 0, end: 6.1, text: 'alpha bravo charlie delta echo foxtrot' },
    ]);
    expect(screen.getByText('0:00.0 - 0:06.1')).toBeTruthy();
    expect(screen.queryByText('0:00.0 - 0:02.9')).toBeNull();
  });

  it('replaces the current (unfrozen) run on a new selection without touching frozen runs', async () => {
    const { container, onRunsChange } = renderPanel();
    await waitForTranscript(container);
    const user = userEvent.setup();

    await selectWords(container, 0, 2); // run A: 0 - 2.9
    await user.click(screen.getByRole('button', { name: 'Add another span' }));

    await selectWords(container, 6, 8); // current: golf hotel india (not frozen)
    expect(onRunsChange).toHaveBeenLastCalledWith([
      { start: 0, end: 2.9, text: 'alpha bravo charlie' },
      { start: 10, end: 12.9, text: 'golf hotel india' },
    ]);

    // A second selection replaces the current run again; A is still untouched
    // and the two are NOT merged even though the gap is under 1s -- merge
    // only happens on freeze.
    await selectWords(container, 3, 5);
    expect(onRunsChange).toHaveBeenLastCalledWith([
      { start: 0, end: 2.9, text: 'alpha bravo charlie' },
      { start: 3.2, end: 6.1, text: 'delta echo foxtrot' },
    ]);
  });
});
