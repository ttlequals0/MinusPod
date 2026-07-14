import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ProcessingRunsTable from './ProcessingRunsTable';
import type { EpisodeProcessingRun } from '../api/types';

const statsRun: EpisodeProcessingRun = {
  runNumber: 2,
  processedAt: '2026-07-14T07:36:34Z',
  status: 'completed',
  adsDetected: 6,
  processingDurationSeconds: 735,
  errorMessage: null,
  inputTokens: 71139,
  outputTokens: 4121,
  llmCost: 0.423089,
  stats: {
    mode: 'reprocess',
    downloadedDuration: 3305.7,
    transcriptSegments: 132,
    windows: { total: 7, failed: 0 },
    stageHits: { fingerprint: 0, textPattern: 3, differential: 11, llm: 11 },
    detected: 12,
    markers: { cut: 6, held: 4, notCut: 5 },
    verificationAdsCut: 0,
    secondsRemoved: 609,
  },
};

const legacyRun: EpisodeProcessingRun = {
  runNumber: 1,
  processedAt: '2026-07-14T07:22:04Z',
  status: 'completed',
  adsDetected: 1,
  processingDurationSeconds: 504,
  errorMessage: null,
  inputTokens: 55747,
  outputTokens: 887,
  llmCost: 0.264646,
  stats: null,
};

describe('ProcessingRunsTable', () => {
  it('renders full stats for a run with a blob', () => {
    render(<ProcessingRunsTable runs={[statsRun]} />);
    expect(screen.getByText(/#2/)).toBeTruthy();
    expect(screen.getByText('(reprocess)')).toBeTruthy();
    expect(screen.getByText('7/7')).toBeTruthy();
    expect(screen.getByText('0 fingerprint / 3 text / 11 cross-fetch / 11 LLM')).toBeTruthy();
    expect(screen.getByText('6 cut / 4 held / 5 kept')).toBeTruthy();
    expect(screen.getByText('clean')).toBeTruthy();
  });

  it('falls back to basic columns for runs without a blob', () => {
    render(<ProcessingRunsTable runs={[legacyRun]} />);
    expect(screen.getByText('#1')).toBeTruthy();
    expect(screen.getByText('1 cut')).toBeTruthy();
    // Downloaded, Windows, Stage hits, Removed, Second scan all dash out.
    expect(screen.getAllByText('-')).toHaveLength(5);
  });

  it('notes a large gap between downloaded and declared duration', () => {
    render(<ProcessingRunsTable runs={[statsRun]} rssDuration={2784} />);
    expect(screen.getByText(/longer\s+than the duration the feed declares/)).toBeTruthy();
  });

  it('omits the note when durations agree', () => {
    render(<ProcessingRunsTable runs={[statsRun]} rssDuration={3300} />);
    expect(screen.queryByText(/the duration the feed declares/)).toBeNull();
  });
});
