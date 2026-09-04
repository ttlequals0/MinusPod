import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFinalSegments, getOriginalSegments, type OriginalSegment } from '../api/feeds';
import type { AdSegment, EpisodeDetail } from '../api/types';
import { getErrorMessage } from '../api/client';
import { Modal } from './Modal';
import { btnGhost } from './buttonStyles';
import { focusRing, inputBase, selectBase } from './fieldStyles';
import LoadingSpinner from './LoadingSpinner';
import { formatClock, parseClock } from '../utils/transcriptTime';

type Source = 'original' | 'processed';

// Rows rendered before the reader scrolls, and how many more each time they
// reach the end. A three-hour episode has a few thousand segments.
const PAGE = 300;

// Attribute variants outrank inputBase's border and focus ring, so an
// unparsable time reads red whether or not the field has focus.
const invalidField = 'aria-invalid:border-destructive aria-invalid:ring-2 aria-invalid:ring-destructive aria-invalid:focus-visible:ring-destructive';

interface Props {
  slug: string;
  episodeId: string;
  episode: EpisodeDetail;
  onClose: () => void;
}

function adFor(seg: OriginalSegment, markers: AdSegment[]): AdSegment | undefined {
  return markers.find((m) => seg.end > m.start && seg.start < m.end);
}

function Highlight({ text, needle }: { text: string; needle: string }) {
  if (!needle) return <>{text}</>;
  const lower = text.toLowerCase();
  const parts: React.ReactNode[] = [];
  let from = 0;
  for (let idx = lower.indexOf(needle); idx >= 0; idx = lower.indexOf(needle, from)) {
    parts.push(text.slice(from, idx), <mark key={idx}>{text.slice(idx, idx + needle.length)}</mark>);
    from = idx + needle.length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}

function TranscriptViewer({ slug, episodeId, episode, onClose }: Props) {
  const [source, setSource] = useState<Source>(
    episode.originalTranscriptAvailable ? 'original' : 'processed',
  );
  const [search, setSearch] = useState('');
  const [startText, setStartText] = useState('');
  const [endText, setEndText] = useState('');
  const [highlight, setHighlight] = useState(false);
  const [shown, setShown] = useState(PAGE);

  const original = useQuery({
    queryKey: ['originalSegments', slug, episodeId],
    queryFn: () => getOriginalSegments(slug, episodeId),
    enabled: source === 'original',
  });
  const processed = useQuery({
    queryKey: ['finalSegments', slug, episodeId],
    queryFn: () => getFinalSegments(slug, episodeId),
    enabled: source === 'processed',
  });
  const query = source === 'original' ? original : processed;
  const segments = useMemo(() => query.data?.segments ?? [], [query.data]);
  const markers = episode.adMarkers ?? [];

  const start = parseClock(startText);
  const end = parseClock(endText);
  const startBad = startText.trim() !== '' && start === null;
  const endBad = endText.trim() !== '' && end === null;
  const needle = search.trim().toLowerCase();

  const filtered = useMemo(() => segments.filter((seg) => {
    if (start !== null && seg.end <= start) return false;
    if (end !== null && seg.start >= end) return false;
    return !needle || seg.text.toLowerCase().includes(needle);
  }), [segments, start, end, needle]);
  const visible = filtered.slice(0, shown);

  const onFilter = (apply: () => void) => {
    apply();
    setShown(PAGE);
  };
  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 400) {
      setShown((n) => (n < filtered.length ? n + PAGE : n));
    }
  };

  // Episodes processed before segments were stored only have the plain
  // processed text, so that is what Processed falls back to.
  const settled = query.isSuccess || query.isError;
  const fallback = source === 'processed' && settled && segments.length === 0
    ? episode.transcript
    : undefined;

  return (
    <Modal onClose={onClose} panelClassName="w-full max-w-5xl h-[90vh] flex flex-col text-foreground">
      <div className="flex items-start justify-between gap-3 p-4 border-b border-border">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">Transcript</h2>
          <p className="text-sm text-muted-foreground truncate">{episode.title}</p>
        </div>
        <button
          onClick={onClose}
          className={`px-3 py-1.5 text-sm rounded ${btnGhost} transition-colors ${focusRing}`}
        >
          Close
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 p-4 border-b border-border">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          Source
          <select
            aria-label="Source"
            value={source}
            onChange={(e) => onFilter(() => setSource(e.target.value as Source))}
            className={selectBase}
          >
            <option value="original">Original</option>
            <option value="processed">Processed</option>
          </select>
        </label>
        <input
          type="search"
          aria-label="Search transcript"
          placeholder="Search"
          value={search}
          onChange={(e) => onFilter(() => setSearch(e.target.value))}
          className={`${inputBase} flex-1 min-w-40`}
        />
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          Start
          <input
            aria-label="Start"
            aria-invalid={startBad}
            value={startText}
            placeholder="0:00"
            inputMode="numeric"
            onChange={(e) => onFilter(() => setStartText(e.target.value))}
            className={`${inputBase} ${invalidField} w-24 tabular-nums`}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          End
          <input
            aria-label="End"
            aria-invalid={endBad}
            value={endText}
            placeholder="end"
            inputMode="numeric"
            onChange={(e) => onFilter(() => setEndText(e.target.value))}
            className={`${inputBase} ${invalidField} w-24 tabular-nums`}
          />
        </label>
        {source === 'original' && markers.length > 0 && (
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input
              type="checkbox"
              aria-label="Highlight ads"
              checked={highlight}
              onChange={(e) => setHighlight(e.target.checked)}
              className={focusRing}
            />
            Highlight ads
          </label>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4" onScroll={onScroll}>
        {query.isLoading && <LoadingSpinner className="py-8" />}
        {query.isError && !fallback && (
          <p className="text-sm text-destructive">{getErrorMessage(query.error)}</p>
        )}
        {query.isSuccess && segments.length === 0 && !fallback && (
          <p className="text-sm text-muted-foreground">No transcript segments for this episode.</p>
        )}
        {fallback && (
          <pre className="whitespace-pre-wrap text-sm font-sans">
            <Highlight text={fallback} needle={needle} />
          </pre>
        )}
        {segments.length > 0 && filtered.length === 0 && (
          <p className="text-sm text-muted-foreground">No segments match the current filters.</p>
        )}
        {visible.length > 0 && (
          <ol className="text-sm space-y-px">
            {visible.map((seg, i) => {
              const ad = highlight && source === 'original' ? adFor(seg, markers) : undefined;
              return (
                <li
                  key={`${seg.start}-${i}`}
                  className={`flex gap-3 border-l-2 pl-3 py-1 ${
                    ad ? 'border-l-destructive bg-destructive/10' : 'border-l-transparent'
                  }`}
                >
                  <span className="shrink-0 w-28 tabular-nums text-muted-foreground">
                    {formatClock(seg.start)} - {formatClock(seg.end)}
                  </span>
                  <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
                    <Highlight text={seg.text} needle={needle} />
                  </span>
                  {ad && (
                    <span className="shrink-0 self-start rounded bg-destructive/10 px-1.5 py-0.5 text-xs text-destructive">
                      {ad.sponsor || 'Ad'}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <div className="p-4 border-t border-border text-sm text-muted-foreground">
        {segments.length > 0 && `${filtered.length} of ${segments.length} segments`}
        {fallback && 'Plain text, no timestamps'}
      </div>
    </Modal>
  );
}

export default TranscriptViewer;
