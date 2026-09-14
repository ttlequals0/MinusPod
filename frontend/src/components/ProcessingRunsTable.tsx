import { Fragment, ReactNode, useState } from 'react';
import { EpisodeProcessingRun, LLM_PROVIDER_LABELS, LlmProvider, RunPhaseUsage } from '../api/types';
import { formatCost, formatDateTime } from '../utils/format';
import { formatDuration, formatTokenCount, formatTokenRange } from '../pages/settings/settingsUtils';
import DisclosureButton from './DisclosureButton';
import CostAmount from './CostAmount';

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

// A run total with unpriced phase rows behind it reads as a floor, not as
// the full bill.
function RunCost({ run }: { run: EpisodeProcessingRun }) {
  const unpriced = (run.phases ?? []).filter((p) => p.costUsd == null).length;
  return <CostAmount amount={run.llmCost} unpricedCount={unpriced} unit="phase group" />;
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
function PhaseBreakdown({ run }: { run: EpisodeProcessingRun }) {
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
  // Drives both the header and the gap row's colSpan, so the two can't drift.
  const headers = [
    'Phase', 'Provider', 'Model', 'Input', 'Output',
    ...(hasCache ? ['Cache'] : []),
    ...(hasReasoning ? ['Reasoning'] : []),
    'Cost',
  ];

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-muted-foreground">
          {headers.map((label, i) => (
            <th
              key={label}
              className={`font-medium ${i < 3 ? 'text-left' : 'text-right'} ${i === headers.length - 1 ? 'py-1' : cellClass}`}
            >
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {phases.map((p, i) => (
          <tr key={`${p.phaseKey}-${p.invokingPass}-${p.configuredModel}-${i}`} className="border-t border-border/40">
            <td className={cellClass}>{phaseLabel(p, phaseKeyCounts[p.phaseKey] > 1)}</td>
            <td className={cellClass}>{providerLabel(p.provider)}</td>
            <td
              className={cellClass}
              title={p.returnedModel && p.returnedModel !== p.configuredModel
                ? `Configured as ${p.configuredModel}` : undefined}
            >
              {p.returnedModel ?? p.configuredModel}
            </td>
            <td className={`${cellClass} text-right`}>{formatTokenCount(p.inputTokens)}</td>
            <td className={`${cellClass} text-right`}>{formatTokenCount(p.outputTokens)}</td>
            {hasCache && (
              <td className={`${cellClass} text-right`}>
                {p.cacheReadTokens || p.cacheWriteTokens
                  ? `${formatTokenCount(p.cacheReadTokens)} r / ${formatTokenCount(p.cacheWriteTokens)} w`
                  : '-'}
              </td>
            )}
            {hasReasoning && (
              <td className={`${cellClass} text-right`}>
                {p.reasoningTokens ? formatTokenCount(p.reasoningTokens) : '-'}
              </td>
            )}
            <td className="py-1 text-right">{p.costUsd == null ? 'Unknown' : formatCost(parseFloat(p.costUsd))}</td>
          </tr>
        ))}
        {gaps.map((g) => (
          <tr key={g.phaseKey} className="border-t border-border/40 text-muted-foreground">
            <td className={cellClass}>{capitalize(g.phaseKey)}</td>
            <td className="py-1" colSpan={headers.length - 1}>{g.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
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

      <table className="hidden sm:table w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className={`${HEADER_CLASS} w-6`} aria-hidden="true" />
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
                      className={i === COLUMNS.length - 1 ? 'py-2 whitespace-nowrap' : 'py-2 pr-4 whitespace-nowrap'}
                    >
                      {col.render(run)}
                    </td>
                  ))}
                </tr>
                {expanded && (
                  <tr className="border-b border-border/50 last:border-b-0 bg-muted/20">
                    <td colSpan={COLUMNS.length + 1} className="py-2 px-3">
                      <div className="overflow-x-auto">
                        <PhaseBreakdown run={run} />
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>

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
                <div className="mt-2 overflow-x-auto">
                  <PhaseBreakdown run={run} />
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
