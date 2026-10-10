import { beforeEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CueMarkModal from './CueMarkModal';

const { createMock, previewMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  previewMock: vi.fn(),
}));

vi.mock('./ad-editor/usePeaks', () => ({
  usePeaks: () => ({ peaks: null, peakResolutionMs: 100, peaksError: null }),
}));
vi.mock('../api/cueTemplates', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/cueTemplates')>(),
  createCueTemplate: createMock,
  previewCueTemplate: previewMock,
  getCueCandidates: () => Promise.resolve({ status: 'ready', candidates: [] }),
}));

beforeEach(() => {
  vi.clearAllMocks();
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
