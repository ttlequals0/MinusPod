import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EpisodeLogsCard from './EpisodeLogsCard';
import type { EpisodeProcessingRun } from '../api/types';

vi.mock('./RunLogViewer', () => ({
  default: ({ runNumber, onClose }: { runNumber: number; onClose: () => void }) => (
    <div data-testid="run-log-viewer">
      Viewing run {runNumber}
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

function makeRun(overrides: Partial<EpisodeProcessingRun> = {}): EpisodeProcessingRun {
  return {
    runNumber: 1,
    processedAt: '2026-08-19T12:00:00Z',
    status: 'completed',
    adsDetected: 3,
    processingDurationSeconds: 120,
    errorMessage: null,
    inputTokens: 0,
    outputTokens: 0,
    llmCost: 0,
    hasLog: true,
    stats: null,
    ...overrides,
  };
}

function renderCard(runs: EpisodeProcessingRun[]) {
  return render(<EpisodeLogsCard slug="test-feed" episodeId="abc123def456" runs={runs} />);
}

describe('EpisodeLogsCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists one row per run', () => {
    renderCard([makeRun(), makeRun({ runNumber: 2 })]);
    expect(screen.getByText('Run 1')).toBeTruthy();
    expect(screen.getByText('Run 2')).toBeTruthy();
  });

  it('offers a view button only for runs that stored a log', () => {
    renderCard([makeRun(), makeRun({ runNumber: 2, hasLog: false })]);
    expect(screen.getAllByRole('button', { name: /View log/ })).toHaveLength(1);
    expect(screen.getByText('Not stored')).toBeTruthy();
  });

  it('opens the viewer for the run that was clicked', async () => {
    renderCard([makeRun(), makeRun({ runNumber: 2 })]);
    await userEvent.click(screen.getByRole('button', { name: 'View log for run 2' }));
    expect(screen.getByTestId('run-log-viewer').textContent).toContain('Viewing run 2');
  });

  it('closes the viewer again', async () => {
    renderCard([makeRun()]);
    await userEvent.click(screen.getByRole('button', { name: 'View log for run 1' }));
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByTestId('run-log-viewer')).toBeNull();
  });

  it('says so when no run stored a log', () => {
    renderCard([makeRun({ hasLog: false })]);
    expect(screen.getByText(/No run has stored a log yet/)).toBeTruthy();
  });
});
