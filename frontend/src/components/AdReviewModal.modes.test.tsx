import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import AdReviewModal, { type AdReviewItem } from './AdReviewModal';
import { MODE_CAPABILITIES, type AdReviewMode } from './adReviewModes';

vi.mock('wavesurfer.js', () => ({
  default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) },
}));
vi.mock('wavesurfer.js/dist/plugins/regions.esm.js', () => ({
  default: { create: vi.fn(() => ({ addRegion: vi.fn(() => ({ setOptions: vi.fn() })) })) },
}));
vi.mock('./ad-editor/usePeaks', () => ({
  usePeaks: () => ({ peaks: [0.2, 0.5, 0.3], peakResolutionMs: 100, peaksError: null, loading: false, retry: vi.fn() }),
}));
vi.mock('../api/sponsors', () => ({ getSponsors: vi.fn().mockResolvedValue([]) }));

const ITEM: AdReviewItem = {
  podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 100, end: 190, sponsor: 'Acme',
  reason: 'sponsor read', confidence: 0.9, detectionStage: 'first_pass', patternId: null, correctedBounds: null,
};

function renderMode(mode: AdReviewMode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AdReviewModal item={ITEM} mode={mode} episodeDuration={3600} onClose={vi.fn()} onSubmit={vi.fn()} onSkip={vi.fn()}
        onAudioModeChange={vi.fn()} onAddNew={vi.fn()} onCreate={vi.fn()} onSaveSpan={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe('AdReviewModal mode capabilities', () => {
  it('review mode pins its capability set', () => {
    expect(MODE_CAPABILITIES.review).toEqual({
      ariaLabel: 'Review ad', heading: 'Detected ad', headingOnPhone: false, followsAudioMode: true,
      fullEpisodeWindow: false, audioToggle: true, addNewButton: true, detectionMeta: true, categoryField: true,
      createFields: false, sponsorPrompt: true, decisionShortcuts: true, backdropCloses: true,
      seekIntoAdOnPlay: true, maxSelectionSeconds: null, footer: 'review',
    });
    renderMode('review');
    expect(screen.getByRole('dialog', { name: 'Review ad' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Processed' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add new ad' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Segment category' })).toBeTruthy();
    expect(screen.getByText('Split')).toBeTruthy();
    expect(screen.getByText('C', { selector: 'kbd' })).toBeTruthy();
  });

  it('create mode pins its capability set', () => {
    expect(MODE_CAPABILITIES.create).toEqual({
      ariaLabel: 'Add new ad', heading: 'Add new ad', headingOnPhone: false, followsAudioMode: false,
      fullEpisodeWindow: true, audioToggle: false, addNewButton: false, detectionMeta: false, categoryField: false,
      createFields: true, sponsorPrompt: false, decisionShortcuts: true, backdropCloses: false,
      seekIntoAdOnPlay: false, maxSelectionSeconds: null, footer: 'create',
    });
    renderMode('create');
    expect(screen.getByRole('dialog', { name: 'Add new ad' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Processed' })).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Segment category' })).toBeNull();
    expect(screen.getByText('Save creates a manual ad pattern.')).toBeTruthy();
  });

  it('span mode pins its capability set', () => {
    expect(MODE_CAPABILITIES.span).toEqual({
      ariaLabel: 'Edit edges', heading: 'Edit edges', headingOnPhone: true, followsAudioMode: false,
      fullEpisodeWindow: false, audioToggle: false, addNewButton: false, detectionMeta: false, categoryField: false,
      createFields: false, sponsorPrompt: false, decisionShortcuts: false, backdropCloses: false,
      seekIntoAdOnPlay: true, maxSelectionSeconds: 600, footer: 'span',
    });
    renderMode('span');
    expect(screen.getByRole('dialog', { name: 'Edit edges' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Processed' })).toBeNull();
    expect(screen.queryByText('C', { selector: 'kbd' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Save audio' })).toBeTruthy();
  });
});

describe('AdReviewModal footer placement', () => {
  it.each(['review', 'create', 'span'] as const)('keeps the %s footer outside the scrolling body so it never covers a control', (mode) => {
    renderMode(mode);
    const dialog = screen.getByRole('dialog');
    const body = [...dialog.children].find((c) => c.className.includes('overflow-y-auto'));
    const footer = dialog.lastElementChild as HTMLElement;
    expect(body).toBeDefined();
    expect(footer).not.toBe(body);
    expect(body?.contains(footer)).toBe(false);
    expect(footer.className).toContain('shrink-0');
  });
});
