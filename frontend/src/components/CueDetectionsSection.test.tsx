import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import CueDetectionsSection from './CueDetectionsSection';
import type { CueDetection } from '../api/types';

const toggle = vi.fn();

vi.mock('../hooks/useAuditionPlayer', () => ({
  useAuditionPlayer: () => ({ playingKey: null, toggle, audioElement: null }),
}));
vi.mock('../api/cueDetections', () => ({ setCueDetectionVerdict: vi.fn() }));

const DETECTION: CueDetection = {
  id: 7, source: 'template', start_s: 10, end_s: 14, match_score: 0.9,
  outcome: 'snap', verdict: 'pending',
};

function renderSection(detections: CueDetection[]) {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <CueDetectionsSection slug="example-podcast" episodeId="a1b2c3d4e5f6" detections={detections} />
    </QueryClientProvider>,
  );
}

describe('CueDetectionsSection', () => {
  it('plays a match through the shared audition button', () => {
    renderSection([DETECTION]);
    const play = screen.getByRole('button', { name: 'Play this match' });
    expect(play.className).toContain('w-11');
    fireEvent.click(play);
    expect(toggle).toHaveBeenCalledWith('7', expect.any(String), 10, 14);
  });

  it('uses the shared row action sizing on verdict buttons', () => {
    renderSection([DETECTION]);
    for (const name of ['Confirm', 'Not ad']) {
      expect(screen.getByRole('button', { name }).className).toContain('min-h-11');
    }
  });
});
