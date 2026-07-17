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

function ProcessingRunsTable({ runs, rssDuration }: ProcessingRunsTableProps) {
  const note = rssDeltaNote(runs, rssDuration);

  return (
    <div>
      {note && <p className="text-sm text-muted-foreground mb-3">{note}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className={HEADER_CLASS}>Run</th>
              <th className={HEADER_CLASS}>When</th>
              <th className={HEADER_CLASS}>Result</th>
              <th className={HEADER_CLASS} title="Length of the downloaded copy this run processed">
                Downloaded
              </th>
              <th className={HEADER_CLASS} title="Detection windows the LLM answered">Windows</th>
              <th className={HEADER_CLASS} title="Detections per stage, before validation">
                Stage hits
              </th>
              <th className={HEADER_CLASS}>Ads</th>
              <th className={HEADER_CLASS} title="Ad time cut from the audio">Removed</th>
              <th className={HEADER_CLASS} title="Second scan of the output audio">Second scan</th>
              <th className={HEADER_CLASS}>Tokens</th>
              <th className={`${HEADER_CLASS} pr-0`}>Cost</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const s = run.stats;
              return (
                <tr key={`${run.runNumber}-${run.processedAt}`}
                    className="border-b border-border/50 last:border-b-0">
                  <td className="py-2 pr-4 whitespace-nowrap">
                    #{run.runNumber}
                    {s?.mode && s.mode !== 'auto' && (
                      <span className="text-muted-foreground"> ({s.mode})</span>
                    )}
                    {s?.detectionSkipped && (
                      <span className="text-muted-foreground"> (no ad detection)</span>
                    )}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">{formatDateTime(run.processedAt)}</td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {run.status === 'failed' ? (
                      <span className="text-destructive cursor-help"
                            title={run.errorMessage ?? undefined}>
                        failed
                      </span>
                    ) : 'completed'}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap"
                      title={s?.transcriptSegments != null
                        ? `${s.transcriptSegments} transcript segments`
                        : undefined}>
                    {s?.downloadedDuration ? formatDuration(s.downloadedDuration) : '-'}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {s?.windows?.total
                      ? s.windows.failed
                        ? `${s.windows.total - s.windows.failed}/${s.windows.total} answered`
                        : `${s.windows.total}/${s.windows.total}`
                      : '-'}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {s?.stageHits
                      ? `${s.stageHits.fingerprint} fingerprint / ${s.stageHits.textPattern} text / ` +
                        `${s.stageHits.differential} cross-fetch / ${s.stageHits.llm} LLM`
                      : '-'}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {s?.markers
                      ? `${s.markers.cut} cut / ${s.markers.held} held / ${s.markers.notCut} kept`
                      : `${run.adsDetected} cut`}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {s?.secondsRemoved != null ? formatDuration(s.secondsRemoved) : '-'}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {s?.verificationAdsCut != null
                      ? s.verificationAdsCut === 0
                        ? 'clean'
                        : `${s.verificationAdsCut} more cut`
                      : '-'}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {formatTokenCount(run.inputTokens)} in / {formatTokenCount(run.outputTokens)} out
                  </td>
                  <td className="py-2 whitespace-nowrap">${run.llmCost.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground mt-3">
        Older runs and recuts only carry the basic columns.
      </p>
    </div>
  );
}

export default ProcessingRunsTable;
