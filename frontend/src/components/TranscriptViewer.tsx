import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFinalSegments, getOriginalSegments, getOriginalTranscript, type OriginalSegment } from '../api/feeds';
import type { AdSegment, EpisodeDetail } from '../api/types';
import { ApiError, getErrorMessage } from '../api/client';
import { Modal } from './Modal';
import { btnGhost } from './buttonStyles';
import { focusRing, inputBase, selectBase } from './fieldStyles';
import LoadingSpinner from './LoadingSpinner';
import { formatTimestamp } from '../utils/format';
import { parseTimeInput } from '../utils/adReviewHelpers';
import { usePagedList } from '../hooks/usePagedList';

type Source = 'original' | 'processed';

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

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// A toLowerCase-then-indexOf approach breaks on characters whose lowercase
// form is a different length (e.g. Turkish 'I'), shifting the match off the
// original string. Matching directly against `text` with a case-insensitive
// regex avoids the drift.
function Highlight({ text, needle }: { text: string; needle: string }) {
  if (!needle) return <>{text}</>;
  const re = new RegExp(escapeRegExp(needle), 'gi');
  const parts: React.ReactNode[] = [];
  let from = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    parts.push(text.slice(from, m.index), <mark key={m.index}>{m[0]}</mark>);
    from = m.index + m[0].length;
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

  const query = useQuery({
    queryKey: [source === 'original' ? 'originalSegments' : 'finalSegments', slug, episodeId],
    queryFn: () => (source === 'original' ? getOriginalSegments : getFinalSegments)(slug, episodeId),
  });
  const segments = useMemo(() => query.data?.segments ?? [], [query.data]);
  const markers = episode.adMarkers ?? [];

  const start = parseTimeInput(startText);
  const end = parseTimeInput(endText);
  const startBad = startText.trim() !== '' && start === null;
  const endBad = endText.trim() !== '' && end === null;
  const needle = search.trim().toLowerCase();

  const filtered = useMemo(() => segments.filter((seg) => {
    if (start !== null && seg.end <= start) return false;
    if (end !== null && seg.start >= end) return false;
    return !needle || seg.text.toLowerCase().includes(needle);
  }), [segments, start, end, needle]);
  const { shown, reset, onScroll } = usePagedList(filtered.length);
  const visible = filtered.slice(0, shown);

  const onFilter = (apply: () => void) => {
    apply();
    reset();
  };

  const status = query.error instanceof ApiError ? query.error.status : undefined;
  const original404 = source === 'original' && query.isError && status === 404;

  // Original segments 404 (never stored as JSON) falls back to the plain
  // retained transcript via the older endpoint. Only fetched once the
  // segments query actually 404s, not on every render of Original.
  const transcriptQuery = useQuery({
    queryKey: ['originalTranscript', slug, episodeId],
    queryFn: () => getOriginalTranscript(slug, episodeId),
    enabled: original404,
  });

  // Episodes processed before segments were stored only have the plain
  // processed text, so that is what Processed falls back to, but only on
  // an empty result or a 404; any other error (e.g. a 500) must still show
  // as an error, not silently swap in stale plain text.
  const processedFallbackReady = source === 'processed'
    && ((query.isSuccess && segments.length === 0) || status === 404);
  const fallback = processedFallbackReady
    ? episode.transcript
    : original404 && transcriptQuery.isSuccess ? transcriptQuery.data : undefined;
  const fallbackPending = original404 && transcriptQuery.isLoading;
  const fallbackError = original404 && transcriptQuery.isError ? transcriptQuery.error : undefined;

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
        {(query.isLoading || fallbackPending) && <LoadingSpinner className="py-8" />}
        {fallbackError && (
          <p className="text-sm text-destructive">{getErrorMessage(fallbackError)}</p>
        )}
        {query.isError && !original404 && !fallback && (
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
                    {formatTimestamp(seg.start)} - {formatTimestamp(seg.end)}
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
