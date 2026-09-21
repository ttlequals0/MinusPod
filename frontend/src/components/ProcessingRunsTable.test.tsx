import { fireEvent, render, screen, within } from '@testing-library/react';
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
    timings: {
      downloadSeconds: 42,
      transcriptionSeconds: 180,
      cutSeconds: 8,
      ffmpegSeconds: 12,
    },
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

const skipDetectionRun: EpisodeProcessingRun = {
  runNumber: 3,
  processedAt: '2026-07-17T10:00:00Z',
  status: 'completed',
  adsDetected: 0,
  processingDurationSeconds: 120,
  errorMessage: null,
  inputTokens: 0,
  outputTokens: 0,
  llmCost: 0,
  stats: {
    mode: 'auto',
    detectionSkipped: true,
    downloadedDuration: 3305.7,
    transcriptSegments: 132,
    markers: { cut: 0, held: 0, notCut: 0 },
    secondsRemoved: 0,
  },
};

const skipVerificationRun: EpisodeProcessingRun = {
  ...skipDetectionRun,
  runNumber: 4,
  stats: {
    mode: 'auto',
    verificationSkipped: true,
    downloadedDuration: 3305.7,
    transcriptSegments: 132,
    timings: { verificationSeconds: 0.01, ffmpegSeconds: 0 },
    markers: { cut: 3, held: 0, notCut: 1 },
    secondsRemoved: 180,
  },
};

const cueOnlyRun: EpisodeProcessingRun = {
  ...skipDetectionRun,
  runNumber: 5,
  stats: {
    mode: 'auto',
    cueOnly: true,
    transcriptionSkipped: true,
    downloadedDuration: 3305.7,
    markers: { cut: 2, held: 0, notCut: 0 },
    secondsRemoved: 120,
  },
};

const phaseRun: EpisodeProcessingRun = {
  runNumber: 6,
  processedAt: '2026-08-01T00:00:00Z',
  status: 'completed',
  adsDetected: 2,
  processingDurationSeconds: 300,
  errorMessage: null,
  inputTokens: 5000,
  outputTokens: 800,
  llmCost: 0.05,
  stats: null,
  breakdownAvailable: true,
  phases: [
    {
      phaseKey: 'detection', invokingPass: 1, provider: 'anthropic',
      configuredModel: 'claude-3-5-sonnet', returnedModel: 'claude-3-5-sonnet',
      inputTokens: 3000, outputTokens: 500, cacheReadTokens: 0, cacheWriteTokens: 0,
      reasoningTokens: 0, costUsd: '0.03', costSource: 'provider_reported',
    },
    {
      phaseKey: 'verification', invokingPass: 2, provider: 'openrouter',
      configuredModel: 'gpt-4o-mini', returnedModel: null,
      inputTokens: 2000, outputTokens: 300, cacheReadTokens: 0, cacheWriteTokens: 0,
      reasoningTokens: 0, costUsd: '0.02', costSource: 'estimated',
    },
  ],
};

const retryPhaseRun: EpisodeProcessingRun = {
  ...phaseRun,
  runNumber: 7,
  phases: [
    {
      phaseKey: 'detection', invokingPass: 1, provider: 'anthropic',
      configuredModel: 'claude-3-5-sonnet', returnedModel: null,
      inputTokens: 1000, outputTokens: 100, cacheReadTokens: 0, cacheWriteTokens: 0,
      reasoningTokens: 0, costUsd: null, costSource: 'unknown',
    },
    {
      phaseKey: 'detection', invokingPass: 1, provider: 'anthropic',
      configuredModel: 'claude-3-haiku', returnedModel: 'claude-3-haiku-20240307',
      inputTokens: 900, outputTokens: 90, cacheReadTokens: 0, cacheWriteTokens: 0,
      reasoningTokens: 0, costUsd: '0.01', costSource: 'provider_reported',
    },
  ],
};

// Every run renders twice: the desktop table and the mobile card stack.
// Scope assertions to the table so a match is unambiguous.
function renderTable(runs: EpisodeProcessingRun[], rssDuration?: number) {
  const { container } = render(<ProcessingRunsTable runs={runs} rssDuration={rssDuration} />);
  return within(container.querySelector('table') as HTMLTableElement);
}

describe('ProcessingRunsTable', () => {
  it('renders full stats for a run with a blob', () => {
    const table = renderTable([statsRun]);
    expect(table.getByText(/#2/)).toBeTruthy();
    expect(table.getByText('(reprocess)')).toBeTruthy();
    expect(table.getByText('7/7')).toBeTruthy();
    expect(table.getByText('0 fingerprint / 3 text / 11 cross-fetch / 11 LLM')).toBeTruthy();
    expect(table.getByText('6 cut / 4 held / 5 kept')).toBeTruthy();
    expect(table.getByText('clean')).toBeTruthy();
  });

  it('falls back to basic columns for runs without a blob', () => {
    const table = renderTable([legacyRun]);
    expect(table.getByText('#1')).toBeTruthy();
    expect(table.getByText('1 cut')).toBeTruthy();
    // Downloaded, Windows, Stage hits, Removed, Second scan all dash out.
    expect(table.getAllByText('-')).toHaveLength(5);
  });

  it('shows elapsed stage timings when the run has them', () => {
    const table = renderTable([statsRun]);
    fireEvent.click(table.getByRole('button', { name: /show phase breakdown for run #2/i }));
    expect(table.getByText('Elapsed by stage')).toBeTruthy();
    expect(table.getByText('Elapsed by stage').parentElement?.className).toContain('sm:w-[calc(100cqw-1.5rem)]');
    expect(table.getByText(/Stage times can overlap/)).toBeTruthy();
    expect(table.getByText('0:42')).toBeTruthy();
    expect(table.getByText('0:12')).toBeTruthy();
    expect(table.getAllByText('Unavailable').length).toBeGreaterThan(0);
  });

  it('shows the Chapters row with a value, and Unavailable when absent', () => {
    const withChapters: EpisodeProcessingRun = {
      ...statsRun,
      runNumber: 8,
      stats: {
        ...statsRun.stats!,
        timings: { ...statsRun.stats!.timings, chaptersSeconds: 5 },
      },
    };
    const withValue = renderTable([withChapters]);
    fireEvent.click(withValue.getByRole('button', { name: /show phase breakdown for run #8/i }));
    expect(withValue.getByText('Chapters').nextSibling?.textContent).toBe('0:05');

    const withoutValue = renderTable([statsRun]);
    fireEvent.click(withoutValue.getByRole('button', { name: /show phase breakdown for run #2/i }));
    expect(withoutValue.getByText('Chapters').nextSibling?.textContent).toBe('Unavailable');
  });

  it('marks a skip-detection run instead of showing zero stage hits', () => {
    const table = renderTable([skipDetectionRun]);
    expect(table.getByText('(no ad detection)')).toBeTruthy();
    expect(table.getByText('0 cut / 0 held / 0 kept')).toBeTruthy();
    // Windows, Stage hits, Second scan dash out: those stages never ran.
    expect(table.queryByText(/fingerprint/)).toBeNull();
    expect(table.queryByText('clean')).toBeNull();
  });

  it('marks a skip-verification run instead of showing a clean second scan', () => {
    const table = renderTable([skipVerificationRun]);
    expect(table.getByText('(no verification)')).toBeTruthy();
    expect(table.getByText('3 cut / 0 held / 1 kept')).toBeTruthy();
    expect(table.queryByText('clean')).toBeNull();
    fireEvent.click(table.getByRole('button', { name: /show phase breakdown for run #4/i }));
    expect(table.getByText('Skipped')).toBeTruthy();
    expect(table.getByText('0:00')).toBeTruthy();
  });

  it('marks a cue-only run with no transcript', () => {
    const table = renderTable([cueOnlyRun]);
    expect(table.getByText('(cue-only)')).toBeTruthy();
    expect(table.getByText('(no transcript)')).toBeTruthy();
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

describe('ProcessingRunsTable: phase breakdown', () => {
  it('is collapsed by default', () => {
    const table = renderTable([phaseRun]);
    expect(table.queryByText('Detection')).toBeNull();
  });

  it('expands to show a provider/model row per phase, no pass suffix when unique', () => {
    const table = renderTable([phaseRun]);
    fireEvent.click(table.getByRole('button', { name: /show phase breakdown for run #6/i }));
    // Each phase appears once in this run, so the "(pass N)" suffix is omitted.
    expect(table.getByText('Detection')).toBeTruthy();
    expect(table.getByText('Verification')).toBeTruthy();
    expect(table.queryByText('Detection (pass 1)')).toBeNull();
    expect(table.queryByText('Verification (pass 2)')).toBeNull();
    expect(table.getByText('Anthropic')).toBeTruthy();
    expect(table.getByText('OpenRouter')).toBeTruthy();
    expect(table.getByText('claude-3-5-sonnet')).toBeTruthy();
    expect(table.getByText('gpt-4o-mini')).toBeTruthy();
  });

  it('shows every model row for a phase retried with a fallback model', () => {
    const table = renderTable([retryPhaseRun]);
    fireEvent.click(table.getByRole('button', { name: /show phase breakdown for run #7/i }));
    expect(table.getAllByText('Detection (pass 1)')).toHaveLength(2);
    expect(table.getByText('claude-3-5-sonnet')).toBeTruthy();
    expect(table.getByText('claude-3-haiku-20240307')).toBeTruthy();
    // First model row's cost is unknown; the fallback's is known.
    expect(table.getByText('Unknown')).toBeTruthy();
    expect(table.getByText('$0.0100')).toBeTruthy();
  });

  it('shows "Breakdown unavailable" for a legacy run with no ledger data', () => {
    const table = renderTable([legacyRun]);
    fireEvent.click(table.getByRole('button', { name: /show phase breakdown for run #1/i }));
    expect(table.getByText('Breakdown unavailable')).toBeTruthy();
    expect(table.getByText('Timing unavailable for this run')).toBeTruthy();
  });
});

describe('ProcessingRunsTable: incomplete run cost', () => {
  it('labels the run total as known spend when a phase has no recorded cost', () => {
    const table = renderTable([retryPhaseRun]);
    expect(table.getAllByText(/Known \$/).length).toBeGreaterThan(0);
    expect(table.getAllByText('Incomplete').length).toBeGreaterThan(0);
  });

  it('shows a plain amount when every phase is priced', () => {
    const table = renderTable([phaseRun]);
    expect(table.queryByText('Incomplete')).toBeNull();
    expect(table.getAllByText('$0.0500').length).toBeGreaterThan(0);
  });
});

describe('ProcessingRunsTable: wide-table layout', () => {
  it('scrolls the desktop table instead of letting a parent clip it', () => {
    const { container } = render(<ProcessingRunsTable runs={[statsRun]} />);
    const table = container.querySelector('table') as HTMLTableElement;
    expect(table.parentElement?.className).toContain('overflow-x-auto');
  });

  it('drops the low-priority columns below lg', () => {
    const table = renderTable([statsRun]);
    for (const label of ['Downloaded', 'Windows', 'Stage hits', 'Second scan']) {
      expect(table.getByRole('columnheader', { name: label }).className).toContain('hidden lg:table-cell');
    }
    expect(table.getByRole('columnheader', { name: 'Cost' }).className).not.toContain('hidden');
  });
});

describe('failed run error disclosure', () => {
  const failedRun: EpisodeProcessingRun = {
    runNumber: 4,
    processedAt: '2026-07-18T10:00:00Z',
    status: 'failed',
    adsDetected: 0,
    processingDurationSeconds: 12,
    errorMessage: 'Whisper endpoint returned 503',
    inputTokens: 0,
    outputTokens: 0,
    llmCost: 0,
    stats: null,
  };

  it('hides the reason behind a focusable button rather than a hover title', () => {
    render(<ProcessingRunsTable runs={[failedRun]} />);
    const toggles = screen.getAllByRole('button', { name: /failed/i });
    expect(toggles[0].getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('Whisper endpoint returned 503')).toBeNull();
  });

  it('reveals the error text and a copy action when expanded', () => {
    render(<ProcessingRunsTable runs={[failedRun]} />);
    fireEvent.click(screen.getAllByRole('button', { name: /failed/i })[0]);

    expect(screen.getAllByText('Whisper endpoint returned 503').length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: 'Copy error' }).length).toBeGreaterThan(0);
  });

  it('renders a plain label when a failure carries no message', () => {
    render(<ProcessingRunsTable runs={[{ ...failedRun, errorMessage: null }]} />);
    expect(screen.queryByRole('button', { name: /failed/i })).toBeNull();
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0);
  });
});
