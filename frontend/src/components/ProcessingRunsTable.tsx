import { Fragment, ReactNode, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { EpisodeProcessingRun, LLM_PROVIDER_LABELS, LlmProvider, RunPhaseUsage } from '../api/types';
import { formatCost, formatDateTime } from '../utils/format';
import { formatDuration, formatTokenCount, formatTokenRange } from '../pages/settings/settingsUtils';
import DisclosureButton from './DisclosureButton';
import CopyButton from './CopyButton';
import CostAmount from './CostAmount';
import { focusRing } from './fieldStyles';

interface ProcessingRunsTableProps {
  runs: EpisodeProcessingRun[];
  // Feed-declared episode duration (itunes:duration), for the DAI note.
  rssDuration?: number | null;
}

// Downloaded copies routinely differ from the feed's declared duration by a
// few seconds; only a gap of minutes signals varying DAI fill.
const RSS_DELTA_NOTE_SECONDS = 120;

const TIMING_STAGES = [
  ['downloadSeconds', 'Download'],
  ['transcriptionSeconds', 'Transcription'],
  ['differentialSeconds', 'Differential'],
  ['audioAnalysisSeconds', 'Audio analysis'],
  ['detectionSeconds', 'Detection'],
  ['refineValidateSeconds', 'Refine and validate'],
  ['cutSeconds', 'Cut'],
  ['verificationSeconds', 'Verification'],
  ['normalizationSeconds', 'Normalization'],
  ['assetsSeconds', 'Assets'],
  ['finalizeSeconds', 'Save episode and feed'],
  ['ffmpegSeconds', 'FFmpeg'],
] as const;

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

// A run total with unpriced phase rows behind it reads as a floor, not as
// the full bill.
function RunCost({ run }: { run: EpisodeProcessingRun }) {
  const unpriced = (run.phases ?? []).filter((p) => p.costUsd == null).length;
  return <CostAmount amount={run.llmCost} unpricedCount={unpriced} unit="phase group" />;
}

// The label is a real disclosure, so touch and keyboard reach the reason a
// hover title used to hide, and the copy action lifts it out for a report.
function RunResult({ run }: { run: EpisodeProcessingRun }) {
  const [open, setOpen] = useState(false);
  if (run.status !== 'failed') return <>completed</>;
  if (!run.errorMessage) return <span className="text-destructive">failed</span>;
  const panelId = `run-error-${run.runNumber}`;
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        className={`inline-flex items-center gap-1 min-h-11 sm:min-h-0 text-destructive underline decoration-dotted underline-offset-2 ${focusRing}`}
      >
        failed
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <div id={panelId} className="w-full max-w-xs whitespace-normal break-words rounded border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
          <p className="mb-1">{run.errorMessage}</p>
          <CopyButton text={run.errorMessage} label="Copy error" copiedLabel="Error copied" />
        </div>
      )}
    </div>
  );
}

function timingValue(run: EpisodeProcessingRun, key: typeof TIMING_STAGES[number][0]): string {
  const timings = run.stats?.timings;
  if (key === 'transcriptionSeconds' && run.stats?.transcriptionSkipped) return 'Skipped';
  if (key === 'detectionSeconds' && run.stats?.detectionSkipped) return 'Skipped';
  if (key === 'verificationSeconds' && run.stats?.verificationSkipped) return 'Skipped';
  if ((key === 'detectionSeconds' || key === 'verificationSeconds') && run.stats?.cueOnly) {
    return 'Not applicable';
  }
  const value = timings?.[key];
  if (value != null) return formatDuration(value);
  if (!timings) return 'Timing unavailable';
  return 'Unavailable';
}

function TimingBreakdown({ run }: { run: EpisodeProcessingRun }) {
  if (!run.stats?.timings) {
    return <p className="mt-3 border-t border-border/40 pt-2 text-xs text-muted-foreground">Timing unavailable for this run</p>;
  }
  return (
    <div className="mt-3 w-full border-t border-border/40 pt-2 sm:w-[calc(100cqw-1.5rem)]">
      <h4 className="text-xs font-medium text-muted-foreground">Elapsed by stage</h4>
      <p className="mt-1 text-xs text-muted-foreground">
        Stage times can overlap. FFmpeg runs inside stages and totals every FFmpeg task in the run, including retries.
      </p>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs tabular-nums sm:grid-cols-2 lg:grid-cols-3">
        {TIMING_STAGES.map(([key, label]) => (
          <div key={key} className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right">{timingValue(run, key)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const HEADER_CLASS = 'py-2 pr-4 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider';

interface Column {
  label: string;
  title?: string;
  // Dropped from the table between sm and lg; the mobile cards still list it.
  lowPriority?: boolean;
  render: (run: EpisodeProcessingRun) => ReactNode;
}

const LOW_PRIORITY_CLASS = 'hidden lg:table-cell';

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
  { label: 'Result', render: (run) => <RunResult run={run} /> },
  {
    label: 'Duration',
    title: 'Wall-clock time for the whole run',
    lowPriority: true,
    render: (run) => (run.processingDurationSeconds != null
      ? formatDuration(run.processingDurationSeconds) : '-'),
  },
  {
    label: 'Downloaded',
    title: 'Length of the downloaded copy this run processed',
    lowPriority: true,
    render: (run) => (run.stats?.downloadedDuration ? formatDuration(run.stats.downloadedDuration) : '-'),
  },
  {
    label: 'Windows',
    title: 'Detection windows the LLM answered',
    lowPriority: true,
    render: (run) => {
      const w = run.stats?.windows;
      if (!w?.total) return '-';
      return w.failed ? `${w.total - w.failed}/${w.total} answered` : `${w.total}/${w.total}`;
    },
  },
  {
    label: 'Stage hits',
    title: 'Detections per stage, before validation',
    lowPriority: true,
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
    lowPriority: true,
    render: (run) => {
      const v = run.stats?.verificationAdsCut;
      if (v == null) return '-';
      return v === 0 ? 'clean' : `${v} more cut`;
    },
  },
  {
    label: 'Tokens',
    render: (run) => formatTokenRange(run.inputTokens, run.outputTokens),
  },
  { label: 'Cost', render: (run) => <RunCost run={run} /> },
];

const COLUMN = Object.fromEntries(COLUMNS.map((c) => [c.label, c])) as Record<string, Column>;
// The card shows Run and Result in its header, so the detail list skips them.
const CARD_ROWS = COLUMNS.filter((c) => c.label !== 'Run' && c.label !== 'Result');

function runKey(run: EpisodeProcessingRun): string {
  return `${run.runNumber}-${run.processedAt}`;
}

function providerLabel(provider: string): string {
  return LLM_PROVIDER_LABELS[provider as LlmProvider] ?? provider;
}

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

// The "(pass N)" suffix only earns its place when the same phase ran in more
// than one pass; a phase that appears once reads as just its name.
function phaseLabel(phase: RunPhaseUsage, disambiguate: boolean): string {
  const base = capitalize(phase.phaseKey);
  return disambiguate && phase.invokingPass ? `${base} (pass ${phase.invokingPass})` : base;
}

// Stages the ledger tracks that a run can legitimately skip; a run missing
// one of these says why instead of just omitting the row. Review/chapters
// have no such run-level flag, so they only render when the ledger has them.
const SKIPPABLE_STAGES = ['detection', 'verification'] as const;

function missingStageReason(run: EpisodeProcessingRun, phaseKey: typeof SKIPPABLE_STAGES[number]): string {
  const skipped = phaseKey === 'detection' ? run.stats?.detectionSkipped : run.stats?.verificationSkipped;
  if (skipped) return 'Skipped';
  if (run.stats?.cueOnly) return 'Not applicable';
  return 'Unavailable';
}

function missingStages(run: EpisodeProcessingRun): { phaseKey: string; reason: string }[] {
  const phases = run.phases ?? [];
  return SKIPPABLE_STAGES
    .filter((key) => !phases.some((p) => p.phaseKey === key))
    .map((key) => ({ phaseKey: key, reason: missingStageReason(run, key) }));
}

// Compact per-phase/model breakdown for one run's expanded row. A phase with
// a retry or fallback yields several model rows, one per configured model.
// layout picks the surface: 'table' for the desktop row, 'cards' for the
// mobile stacked view (which wraps instead of scrolling horizontally). Both
// callers stay mounted (CSS-gated); each renders only its own layout.
function PhaseBreakdown({ run, layout }: { run: EpisodeProcessingRun; layout: 'table' | 'cards' }) {
  if (!run.breakdownAvailable) {
    return <p className="text-xs text-muted-foreground py-1">Breakdown unavailable</p>;
  }
  const phases = run.phases ?? [];
  const phaseKeyCounts = phases.reduce<Record<string, number>>((acc, p) => {
    acc[p.phaseKey] = (acc[p.phaseKey] ?? 0) + 1;
    return acc;
  }, {});
  const hasCache = phases.some((p) => p.cacheReadTokens || p.cacheWriteTokens);
  const hasReasoning = phases.some((p) => p.reasoningTokens);
  const gaps = missingStages(run);
  const cellClass = 'py-1 pr-3';
  const modelTitle = (p: RunPhaseUsage) =>
    (p.returnedModel && p.returnedModel !== p.configuredModel ? `Configured as ${p.configuredModel}` : undefined);
  // Metrics after the Phase column, shared by the desktop table and the mobile
  // stacked cards so the two layouts can't drift. left = identity, right = numeric.
  const metrics: { label: string; align: 'left' | 'right'; render: (p: RunPhaseUsage) => ReactNode;
    title?: (p: RunPhaseUsage) => string | undefined }[] = [
    { label: 'Provider', align: 'left', render: (p) => providerLabel(p.provider) },
    { label: 'Model', align: 'left', render: (p) => p.returnedModel ?? p.configuredModel, title: modelTitle },
    { label: 'Input', align: 'right', render: (p) => formatTokenCount(p.inputTokens) },
    { label: 'Output', align: 'right', render: (p) => formatTokenCount(p.outputTokens) },
    ...(hasCache ? [{ label: 'Cache', align: 'right' as const, render: (p: RunPhaseUsage) => (
      p.cacheReadTokens || p.cacheWriteTokens
        ? `${formatTokenCount(p.cacheReadTokens)} r / ${formatTokenCount(p.cacheWriteTokens)} w` : '-') }] : []),
    ...(hasReasoning ? [{ label: 'Reasoning', align: 'right' as const,
      render: (p: RunPhaseUsage) => (p.reasoningTokens ? formatTokenCount(p.reasoningTokens) : '-') }] : []),
    { label: 'Cost', align: 'right', render: (p) => (p.costUsd == null ? 'Unknown' : formatCost(parseFloat(p.costUsd))) },
  ];
  const rowKey = (p: RunPhaseUsage, i: number) => `${p.phaseKey}-${p.invokingPass}-${p.configuredModel}-${i}`;
  const colClass = (m: typeof metrics[number], i: number) =>
    `${m.align === 'right' ? 'text-right' : 'text-left'} ${i === metrics.length - 1 ? 'py-1' : cellClass}`;

  if (layout === 'table') {
    return (
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted-foreground">
            <th className={`font-medium text-left ${cellClass}`}>Phase</th>
            {metrics.map((m, i) => (
              <th key={m.label} className={`font-medium ${colClass(m, i)}`}>
                {m.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {phases.map((p, i) => (
            <tr key={rowKey(p, i)} className="border-t border-border/40">
              <td className={cellClass}>{phaseLabel(p, phaseKeyCounts[p.phaseKey] > 1)}</td>
              {metrics.map((m, j) => (
                <td
                  key={m.label}
                  title={m.title?.(p)}
                  className={colClass(m, j)}
                >
                  {m.render(p)}
                </td>
              ))}
            </tr>
          ))}
          {gaps.map((g) => (
            <tr key={g.phaseKey} className="border-t border-border/40 text-muted-foreground">
              <td className={cellClass}>{capitalize(g.phaseKey)}</td>
              <td className="py-1" colSpan={metrics.length}>{g.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <div className="space-y-2">
      {phases.map((p, i) => (
          <div key={rowKey(p, i)} className="rounded border border-border/40 p-2 text-xs">
            <div className="font-medium mb-1">{phaseLabel(p, phaseKeyCounts[p.phaseKey] > 1)}</div>
            <dl className="space-y-0.5">
              {metrics.map((m) => (
                <div key={m.label} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground shrink-0">{m.label}</dt>
                  <dd className="text-right break-all" title={m.title?.(p)}>{m.render(p)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
        {gaps.map((g) => (
          <div key={g.phaseKey} className="rounded border border-border/40 p-2 text-xs flex justify-between gap-2 text-muted-foreground">
            <span className="font-medium">{capitalize(g.phaseKey)}</span>
            <span>{g.reason}</span>
          </div>
        ))}
    </div>
  );
}

// Shared by the desktop table cell and the mobile card so the toggle's
// label logic can't drift between the two layouts.
function PhaseDisclosureButton({
  run, expanded, onToggle, showLabel,
}: { run: EpisodeProcessingRun; expanded: boolean; onToggle: () => void; showLabel?: boolean }) {
  const label = expanded
    ? `Hide phase breakdown for run #${run.runNumber}`
    : `Show phase breakdown for run #${run.runNumber}`;
  return (
    <DisclosureButton
      expanded={expanded}
      onToggle={onToggle}
      label={label}
      showLabel={showLabel}
    />
  );
}

function ProcessingRunsTable({ runs, rssDuration }: ProcessingRunsTableProps) {
  const note = rssDeltaNote(runs, rssDuration);
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());
  const toggleExpanded = (key: string) => setExpandedRuns((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <div>
      {note && <p className="text-sm text-muted-foreground mb-3">{note}</p>}

      <div className="hidden sm:block overflow-x-auto" style={{ containerType: 'inline-size' }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className={`${HEADER_CLASS} w-6`} aria-hidden="true" />
              {COLUMNS.map((col, i) => (
                <th
                  key={col.label}
                  title={col.title}
                  className={`${i === COLUMNS.length - 1 ? `${HEADER_CLASS} pr-0` : HEADER_CLASS}${col.lowPriority ? ` ${LOW_PRIORITY_CLASS}` : ''}`}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const key = runKey(run);
              const expanded = expandedRuns.has(key);
              return (
                <Fragment key={key}>
                  <tr className="border-b border-border/50 last:border-b-0">
                    <td className="py-2 pr-2">
                      <PhaseDisclosureButton run={run} expanded={expanded} onToggle={() => toggleExpanded(key)} />
                    </td>
                    {COLUMNS.map((col, i) => (
                      <td
                        key={col.label}
                        title={col.label === 'Downloaded' && run.stats?.transcriptSegments != null
                          ? `${run.stats.transcriptSegments} transcript segments`
                          : undefined}
                        className={`${i === COLUMNS.length - 1 ? 'py-2 whitespace-nowrap' : 'py-2 pr-4 whitespace-nowrap'}${col.lowPriority ? ` ${LOW_PRIORITY_CLASS}` : ''}`}
                      >
                        {col.render(run)}
                      </td>
                    ))}
                  </tr>
                  {expanded && (
                    <tr className="border-b border-border/50 last:border-b-0 bg-muted/20">
                      <td colSpan={COLUMNS.length + 1} className="py-2 px-3">
                        <div className="overflow-x-auto">
                          <PhaseBreakdown run={run} layout="table" />
                        </div>
                        <TimingBreakdown run={run} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="sm:hidden space-y-3">
        {runs.map((run) => {
          const key = runKey(run);
          const expanded = expandedRuns.has(key);
          return (
            <div key={key} className="bg-card border border-border rounded-lg p-4 text-sm">
              <div className="flex items-center justify-between gap-2 mb-2 font-medium">
                <span>{COLUMN.Run.render(run)}</span>
                <span>{COLUMN.Result.render(run)}</span>
              </div>
              <dl className="space-y-1">
                {CARD_ROWS.map((col) => (
                  <div key={col.label} className="flex justify-between gap-3">
                    <dt className="text-muted-foreground shrink-0">{col.label}</dt>
                    <dd className="text-right">{col.render(run)}</dd>
                  </div>
                ))}
              </dl>
              <div className="mt-2">
                <PhaseDisclosureButton run={run} expanded={expanded} onToggle={() => toggleExpanded(key)} showLabel />
              </div>
              {expanded && (
                <div className="mt-2">
                  <PhaseBreakdown run={run} layout="cards" />
                  <TimingBreakdown run={run} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ProcessingRunsTable;
