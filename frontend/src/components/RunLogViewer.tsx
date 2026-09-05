import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Modal } from './Modal';
import LoadingSpinner from './LoadingSpinner';
import { btnGhost, btnOutline } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { episodeRunLogDownloadUrl, getEpisodeRunLog } from '../api/feeds';
import type { RunLogLine } from '../api/types';
import { usePagedList } from '../hooks/usePagedList';

interface RunLogViewerProps {
  slug: string;
  episodeId: string;
  runNumber: number;
  onClose: () => void;
}

const LEVELS = ['debug', 'info', 'warning', 'error'] as const;
type Level = typeof LEVELS[number];

// Chip each known level answers to; critical rides the error chip. A level
// missing here (custom handlers) matches no chip and is never hidden.
const LEVEL_CHIP: Record<string, Level> = {
  debug: 'debug', info: 'info', warning: 'warning', error: 'error', critical: 'error',
};
const LEVEL_LABEL: Record<Level, string> = {
  debug: 'Debug', info: 'Info', warning: 'Warning', error: 'Error',
};
// The left rail carries severity so the eye can scan the gutter, not the text.
const LEVEL_STYLE: Record<string, { rail: string; tag: string }> = {
  debug: { rail: 'border-l-border', tag: 'text-muted-foreground' },
  info: { rail: 'border-l-border', tag: 'text-foreground' },
  warning: { rail: 'border-l-warning', tag: 'text-warning' },
  error: { rail: 'border-l-destructive', tag: 'text-destructive' },
  critical: { rail: 'border-l-destructive', tag: 'text-destructive' },
};

function clockTime(ts: string): string {
  const at = new Date(ts);
  return Number.isNaN(at.getTime()) ? ts : at.toISOString().slice(11, 19);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function RunLogViewer({ slug, episodeId, runNumber, onClose }: RunLogViewerProps) {
  // No chips selected means no level filter; selecting chips keeps only
  // those levels. Matches how the pills read at a glance.
  const [selectedLevels, setSelectedLevels] = useState<ReadonlySet<Level>>(new Set());
  const [search, setSearch] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['runLog', slug, episodeId, runNumber],
    queryFn: () => getEpisodeRunLog(slug, episodeId, runNumber),
  });

  const lines: RunLogLine[] = useMemo(() => data?.lines ?? [], [data]);
  // Filtered here rather than through the endpoint's own level param, so
  // switching chips costs nothing; the server filter serves API callers.
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return lines.filter((line) => {
      if (selectedLevels.size > 0) {
        const chip = LEVEL_CHIP[line.level?.toLowerCase()];
        if (chip !== undefined && !selectedLevels.has(chip)) return false;
      }
      return !needle || line.msg.toLowerCase().includes(needle);
    });
  }, [lines, selectedLevels, search]);

  const { shown, reset, onScroll } = usePagedList(filtered.length);
  const visible = filtered.slice(0, shown);

  const onFilterChange = (apply: () => void) => {
    apply();
    reset();
  };

  return (
    <Modal onClose={onClose} panelClassName="w-full max-w-5xl h-[90vh] flex flex-col text-foreground">
      <div className="flex items-start justify-between gap-3 p-4 border-b border-border">
        <div>
          <h2 className="text-lg font-semibold">Run {runNumber} log</h2>
          <p className="text-sm text-muted-foreground">
            {data ? `${lines.length} lines, ${formatBytes(data.bytes)}` : 'Pipeline log for this run'}
          </p>
        </div>
        <button
          onClick={onClose}
          className={`px-3 py-1.5 text-sm rounded ${btnGhost} transition-colors ${focusRing}`}
        >
          Close
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 p-4 border-b border-border">
        <div className="flex items-center gap-1" role="group" aria-label="Filter by level">
          {LEVELS.map((level) => (
            <button
              key={level}
              aria-pressed={selectedLevels.has(level)}
              onClick={() => onFilterChange(() => setSelectedLevels((prev) => {
                const next = new Set(prev);
                if (next.has(level)) next.delete(level); else next.add(level);
                return next;
              }))}
              className={`px-2.5 py-1 text-xs rounded transition-colors ${focusRing} ${
                selectedLevels.has(level) ? 'bg-primary text-primary-foreground' : btnOutline
              }`}
            >
              {LEVEL_LABEL[level]}
            </button>
          ))}
        </div>
        <input
          type="search"
          aria-label="Search this log"
          placeholder="Search lines"
          value={search}
          onChange={(e) => onFilterChange(() => setSearch(e.target.value))}
          className={`flex-1 min-w-40 px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground border border-border ${focusRing}`}
        />
        <a
          href={episodeRunLogDownloadUrl(slug, episodeId, runNumber)}
          download
          className={`px-3 py-1.5 text-sm rounded ${btnOutline} transition-colors ${focusRing}`}
        >
          Download
        </a>
      </div>

      <div className="flex-1 overflow-y-auto p-4" onScroll={onScroll}>
        {isLoading && <LoadingSpinner className="py-8" />}
        {error && (
          <p className="text-sm text-destructive">
            {error instanceof Error ? error.message : 'This log could not be loaded.'}
          </p>
        )}
        {!isLoading && !error && filtered.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No lines match the current filters.
          </p>
        )}
        {visible.length > 0 && (
          <ol className="font-mono text-xs space-y-px">
            {visible.map((line, i) => {
              const style = LEVEL_STYLE[line.level?.toLowerCase()] ?? LEVEL_STYLE.info;
              return (
                <li
                  key={`${line.ts}-${i}`}
                  className={`flex flex-col gap-0.5 border-l-2 pl-3 py-1 sm:flex-row sm:gap-3 sm:py-0.5 ${style.rail}`}
                >
                  {/* Narrow screens stack the stamp above its line; sm: drops
                      this wrapper so time and level align as columns again. */}
                  <div className="flex items-baseline gap-3 sm:contents">
                    <span className="text-muted-foreground shrink-0" title={line.ts}>
                      {clockTime(line.ts)}
                    </span>
                    <span className={`shrink-0 uppercase sm:w-16 ${style.tag}`}>{line.level}</span>
                  </div>
                  {/* min-w-0 lets a long URL wrap instead of widening the row. */}
                  <span className="min-w-0 whitespace-pre-wrap break-words">{line.msg}</span>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 p-4 border-t border-border text-sm text-muted-foreground">
        <span>{filtered.length} of {lines.length} lines</span>
        {data?.truncated && (
          <span className="text-warning">This run hit the 20 MB cap, so the rest of the log is missing.</span>
        )}
      </div>
    </Modal>
  );
}

export default RunLogViewer;
