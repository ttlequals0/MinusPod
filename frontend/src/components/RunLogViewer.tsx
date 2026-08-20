import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Modal } from './Modal';
import LoadingSpinner from './LoadingSpinner';
import { btnGhost, btnOutline } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { episodeRunLogDownloadUrl, getEpisodeRunLog } from '../api/feeds';
import type { RunLogLine } from '../api/types';

interface RunLogViewerProps {
  slug: string;
  episodeId: string;
  runNumber: number;
  onClose: () => void;
}

const LEVELS = ['debug', 'info', 'warning', 'error'] as const;
type Level = typeof LEVELS[number];

const LEVEL_RANK: Record<string, number> = { debug: 10, info: 20, warning: 30, error: 40 };
const LEVEL_LABEL: Record<Level, string> = {
  debug: 'Debug', info: 'Info', warning: 'Warning', error: 'Error',
};
// The left rail carries severity so the eye can scan the gutter, not the text.
const LEVEL_STYLE: Record<string, { rail: string; tag: string }> = {
  debug: { rail: 'border-l-border', tag: 'text-muted-foreground' },
  info: { rail: 'border-l-border', tag: 'text-foreground' },
  warning: { rail: 'border-l-warning', tag: 'text-warning' },
  error: { rail: 'border-l-destructive', tag: 'text-destructive' },
};

// Rows rendered before the reader scrolls, and how many more each time they
// reach the end. Keeps a 50k-line log off the first paint.
const PAGE = 300;

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
  const [minLevel, setMinLevel] = useState<Level>('debug');
  const [search, setSearch] = useState('');
  const [shown, setShown] = useState(PAGE);

  const { data, isLoading, error } = useQuery({
    queryKey: ['runLog', slug, episodeId, runNumber],
    queryFn: () => getEpisodeRunLog(slug, episodeId, runNumber),
  });

  const lines: RunLogLine[] = useMemo(() => data?.lines ?? [], [data]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const floor = LEVEL_RANK[minLevel];
    return lines.filter((line) => {
      if ((LEVEL_RANK[line.level?.toLowerCase()] ?? 0) < floor) return false;
      return !needle || line.msg.toLowerCase().includes(needle);
    });
  }, [lines, minLevel, search]);

  const visible = filtered.slice(0, shown);

  const onFilterChange = (apply: () => void) => {
    apply();
    setShown(PAGE);
  };

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 400) {
      setShown((n) => (n < filtered.length ? n + PAGE : n));
    }
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
        <div className="flex items-center gap-1" role="group" aria-label="Minimum level">
          {LEVELS.map((level) => (
            <button
              key={level}
              aria-pressed={minLevel === level}
              onClick={() => onFilterChange(() => setMinLevel(level))}
              className={`px-2.5 py-1 text-xs rounded transition-colors ${focusRing} ${
                minLevel === level ? 'bg-primary text-primary-foreground' : btnOutline
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
            No lines match this level and search.
          </p>
        )}
        {visible.length > 0 && (
          <ol className="font-mono text-xs space-y-px">
            {visible.map((line, i) => {
              const style = LEVEL_STYLE[line.level?.toLowerCase()] ?? LEVEL_STYLE.info;
              return (
                <li
                  key={`${line.ts}-${i}`}
                  className={`flex gap-3 border-l-2 pl-3 py-0.5 ${style.rail}`}
                >
                  <span className="text-muted-foreground shrink-0" title={line.ts}>
                    {clockTime(line.ts)}
                  </span>
                  <span className={`w-16 shrink-0 uppercase ${style.tag}`}>{line.level}</span>
                  <span className="whitespace-pre-wrap break-words">{line.msg}</span>
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
