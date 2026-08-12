import { ReactNode } from 'react';
import { EpisodeProcessingRun } from '../api/types';
import { formatDateTime } from '../utils/format';
import { formatDuration, formatTokenCount } from '../pages/settings/settingsUtils';

interface ProcessingRunsTableProps {
  runs: EpisodeProcessingRun[];
  // Feed-declared episode duration (itunes:duration), for the DAI note.
  rssDuration?: number | null;
}

// Downloaded copies routinely differ from the feed's declared duration by a
// few seconds; only a gap of minutes signals varying DAI fill.
const RSS_DELTA_NOTE_SECONDS = 120;

function rssDeltaNote(runs: EpisodeProcessingRun[], rssDuration?: number | null): string | null {
  // Most recent run that actually downloaded audio: recuts and early
  // failures carry no blob and must not hide the DAI signal.
  const downloaded = [...runs].reverse()
    .map((run) => run.stats?.downloadedDuration)
    .find((d) => d != null);
  if (!downloaded || !rssDuration) return null;
  const delta = downloaded - rssDuration;
  if (Math.abs(delta) < RSS_DELTA_NOTE_SECONDS) return null;
  const direction = delta > 0 ? 'longer' : 'shorter';
  return `The latest downloaded copy is ${formatDuration(Math.abs(delta))} ${direction} ` +
    'than the duration the feed declares. Dynamically inserted ad loads vary per download.';
}

const HEADER_CLASS = 'py-2 pr-4 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider';

interface Column {
  label: string;
  title?: string;
  render: (run: EpisodeProcessingRun) => ReactNode;
}

// One definition drives both the desktop table and the mobile cards, so the
// two can never drift apart.
const COLUMNS: Column[] = [
  {
    label: 'Run',
    render: (run) => {
      const s = run.stats;
      const notes = [
        s?.mode && s.mode !== 'auto' ? s.mode : null,
        s?.detectionSkipped ? 'no ad detection' : null,
        s?.verificationSkipped ? 'no verification' : null,
        s?.cueOnly ? 'cue-only' : null,
        s?.transcriptionSkipped ? 'no transcript' : null,
      ].filter(Boolean);
      return (
        <>
          #{run.runNumber}
          {notes.map((n) => <span key={n} className="text-muted-foreground"> ({n})</span>)}
        </>
      );
    },
  },
  { label: 'When', render: (run) => formatDateTime(run.processedAt) },
  {
    label: 'Result',
    render: (run) => (run.status === 'failed'
      ? <span className="text-destructive cursor-help" title={run.errorMessage ?? undefined}>failed</span>
      : 'completed'),
  },
  {
    label: 'Downloaded',
    title: 'Length of the downloaded copy this run processed',
    render: (run) => (run.stats?.downloadedDuration ? formatDuration(run.stats.downloadedDuration) : '-'),
  },
  {
    label: 'Windows',
    title: 'Detection windows the LLM answered',
    render: (run) => {
      const w = run.stats?.windows;
      if (!w?.total) return '-';
      return w.failed ? `${w.total - w.failed}/${w.total} answered` : `${w.total}/${w.total}`;
    },
  },
  {
    label: 'Stage hits',
    title: 'Detections per stage, before validation',
    render: (run) => {
      const h = run.stats?.stageHits;
      return h
        ? `${h.fingerprint} fingerprint / ${h.textPattern} text / ${h.differential} cross-fetch / ${h.llm} LLM`
        : '-';
    },
  },
  {
    label: 'Ads',
    render: (run) => {
      const m = run.stats?.markers;
      return m ? `${m.cut} cut / ${m.held} held / ${m.notCut} kept` : `${run.adsDetected} cut`;
    },
  },
  {
    label: 'Removed',
    title: 'Ad time cut from the audio',
    render: (run) => (run.stats?.secondsRemoved != null ? formatDuration(run.stats.secondsRemoved) : '-'),
  },
  {
    label: 'Second scan',
    title: 'Second scan of the output audio',
    render: (run) => {
      const v = run.stats?.verificationAdsCut;
      if (v == null) return '-';
      return v === 0 ? 'clean' : `${v} more cut`;
    },
  },
  {
    label: 'Tokens',
    render: (run) => `${formatTokenCount(run.inputTokens)} in / ${formatTokenCount(run.outputTokens)} out`,
  },
  { label: 'Cost', render: (run) => `$${run.llmCost.toFixed(2)}` },
];

function runKey(run: EpisodeProcessingRun): string {
  return `${run.runNumber}-${run.processedAt}`;
}

function ProcessingRunsTable({ runs, rssDuration }: ProcessingRunsTableProps) {
  const note = rssDeltaNote(runs, rssDuration);

  return (
    <div>
      {note && <p className="text-sm text-muted-foreground mb-3">{note}</p>}

      <table className="hidden sm:table w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {COLUMNS.map((col, i) => (
              <th
                key={col.label}
                title={col.title}
                className={i === COLUMNS.length - 1 ? `${HEADER_CLASS} pr-0` : HEADER_CLASS}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={runKey(run)} className="border-b border-border/50 last:border-b-0">
              {COLUMNS.map((col, i) => (
                <td
                  key={col.label}
                  title={col.label === 'Downloaded' && run.stats?.transcriptSegments != null
                    ? `${run.stats.transcriptSegments} transcript segments`
                    : undefined}
                  className={i === COLUMNS.length - 1 ? 'py-2 whitespace-nowrap' : 'py-2 pr-4 whitespace-nowrap'}
                >
                  {col.render(run)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="sm:hidden space-y-3">
        {runs.map((run) => (
          <div key={runKey(run)} className="bg-card border border-border rounded-lg p-4 text-sm">
            <div className="flex items-center justify-between gap-2 mb-2 font-medium">
              <span>{COLUMNS[0].render(run)}</span>
              <span>{COLUMNS[2].render(run)}</span>
            </div>
            <dl className="space-y-1">
              {COLUMNS.slice(1).filter((c) => c.label !== 'Result').map((col) => (
                <div key={col.label} className="flex justify-between gap-3">
                  <dt className="text-muted-foreground shrink-0">{col.label}</dt>
                  <dd className="text-right">{col.render(run)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      <p className="text-xs text-muted-foreground mt-3">
        Older runs and recuts only carry the basic columns.
      </p>
    </div>
  );
}

export default ProcessingRunsTable;
