import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CueMarkModal from './CueMarkModal';

const { createMock, previewMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  previewMock: vi.fn(),
}));

const peaksState = vi.hoisted(() => ({
  value: { peaks: null as number[] | null, peakResolutionMs: 100, peaksError: null as string | null },
  retry: vi.fn(),
}));
vi.mock('wavesurfer.js', () => ({
  default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn(), getWidth: vi.fn(() => 300) })) },
}));
vi.mock('./ad-editor/usePeaks', () => ({
  usePeaks: () => {
    return { ...peaksState.value, loading: !peaksState.value.peaks && !peaksState.value.peaksError, retry: peaksState.retry };
  },
}));
vi.mock('../api/cueTemplates', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/cueTemplates')>(),
  createCueTemplate: createMock,
  previewCueTemplate: previewMock,
  getCueCandidates: () => Promise.resolve({ status: 'ready', candidates: [] }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  peaksState.value = { peaks: null, peakResolutionMs: 100, peaksError: null };
  peaksState.retry.mockClear();
  previewMock.mockResolvedValue({ matches: [] });
});

it.each([
  ['Save cue', true],
  ['Save cue', false],
  ['Save and preview', true],
  ['Save and preview', false],
] as const)('locks the removal preference during %s with removal %s', async (action, removeWithAd) => {
  let finish: (value: { id: number; removeWithAd: boolean }) => void = () => {};
  createMock.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const onClose = vi.fn();
  const onSaved = vi.fn();
  const user = userEvent.setup();
  render(
    <CueMarkModal
      podcastSlug="example-podcast"
      episodeId="a1b2c3d4e5f6"
      episodeTitle="Example"
      episodeDuration={120}
      initialStart={10}
      initialEnd={12}
      onClose={onClose}
      onSaved={onSaved}
    />,
  );
  const removal = screen.getByRole('checkbox', { name: 'Remove cue with ad' });
  if (!removeWithAd) await user.click(screen.getByText('Remove cue with ad'));
  await user.click(screen.getByRole('button', { name: action }));
  expect(createMock).toHaveBeenCalledWith(
    'example-podcast', 'a1b2c3d4e5f6', 10, 12, 'ad_break_boundary', removeWithAd,
  );
  expect(removal).toHaveProperty('disabled', true);
  await user.click(screen.getByText('Remove cue with ad'));
  expect(removal).toHaveProperty('checked', removeWithAd);
  await act(async () => finish({ id: 1, removeWithAd }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ id: 1, removeWithAd }));
  if (action === 'Save cue') {
    expect(onClose).toHaveBeenCalledOnce();
  } else {
    await waitFor(() => expect(removal).toHaveProperty('disabled', false));
    expect(previewMock).toHaveBeenCalledWith('example-podcast', 'a1b2c3d4e5f6', 1);
  }
});

function renderCue() {
  return render(
    <CueMarkModal
      podcastSlug="example-podcast"
      episodeId="a1b2c3d4e5f6"
      episodeTitle="Example"
      episodeDuration={120}
      initialStart={10}
      initialEnd={12}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
}

describe('CueMarkModal waveform states', () => {
  it('gives the snap checkbox its own line on a narrow row and the edge buttons the recipe padding', () => {
    renderCue();
    const row = screen.getByLabelText('Snap to detected boundaries').closest('label')?.parentElement as HTMLElement;
    expect(row.className.split(/\s+/)).toContain('@container');
    expect(row.querySelector('label')?.className).toContain('@max-[26rem]:w-full');
    for (const btn of Array.from(row.querySelectorAll('button'))) expect(btn.className.split(/\s+/)).toContain('px-3');
  });

  it('shows the loading line while peaks are pending, out of the layout flow', () => {
    renderCue();
    const loading = screen.getByText('Loading waveform...');
    expect(loading.className).toContain('absolute');
    expect(screen.queryByText(/Could not load waveform/)).toBeNull();
    expect(screen.queryByRole('slider', { name: /START pin/ })).toBeNull();
  });

  it('drops the loading line once the waveform is loaded', () => {
    peaksState.value = { peaks: [0.2, 0.5, 0.3], peakResolutionMs: 100, peaksError: null };
    renderCue();
    expect(screen.queryByText('Loading waveform...')).toBeNull();
    expect(screen.queryByText(/Could not load waveform/)).toBeNull();
  });

  it('shows the error with a Retry and drops the loading line on failure', () => {
    peaksState.value = { peaks: null, peakResolutionMs: 100, peaksError: 'boom' };
    renderCue();
    expect(screen.getByText('Could not load waveform. Try again.')).toBeTruthy();
    expect(screen.queryByText(/boom/)).toBeNull();
    expect(screen.queryByText('Loading waveform...')).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('refetches when Retry is pressed', async () => {
    peaksState.value = { peaks: null, peakResolutionMs: 100, peaksError: 'boom' };
    renderCue();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(peaksState.retry).toHaveBeenCalledTimes(1);
  });
});
