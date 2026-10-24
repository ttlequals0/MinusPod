import { Fragment, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFinalSegments, getOriginalSegments, getOriginalTranscript, type OriginalSegment } from '../api/feeds';
import type { AdSegment, EpisodeDetail } from '../api/types';
import { ApiError, getErrorMessage } from '../api/client';
import { Modal } from './Modal';
import { SkeletonRows } from './Skeleton';
import { formatTimestamp } from '../utils/format';
import { parseTimeInput } from '../utils/adReviewHelpers';
import { usePagedList } from '../hooks/usePagedList';
import SegmentedToggle from './SegmentedToggle';
import { groupParagraphs } from '../utils/transcriptParagraphs';
import { btnBase, btnGhost } from './buttonStyles';
import { focusRing, inputBase, selectBase, filterLabel, filterControl } from './fieldStyles';
import { badgeBase, tint } from './badgeStyles';

type Source = 'original' | 'processed';
type View = 'paragraphs' | 'lines';

const VIEW_KEY = 'minuspod.transcript.view';
const VIEW_OPTIONS = [
  { value: 'paragraphs', label: 'Paragraphs' },
  { value: 'lines', label: 'Lines' },
] as const;

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'lines' ? 'lines' : 'paragraphs';
  } catch {
    return 'paragraphs';
  }
}

function writeView(v: View) {
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    // storage unavailable: the choice lasts for this session only
  }
}

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

// Matches against `text` directly with a case-insensitive regex, not
// toLowerCase+indexOf, which drifts on chars whose lowercase form changes length (e.g. Turkish 'I').
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
  const [view, setView] = useState<View>(readView);

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
  // An empty End means the whole episode, so its placeholder is the latest segment end.
  const lastEnd = useMemo(
    () => (segments.length > 0 ? formatTimestamp(segments.reduce((m, s) => Math.max(m, s.end), 0)) : 'end'),
    [segments],
  );
  const needle = search.trim().toLowerCase();

  const filtered = useMemo(() => segments.filter((seg) => {
    if (start !== null && seg.end <= start) return false;
    if (end !== null && seg.start >= end) return false;
    return !needle || seg.text.toLowerCase().includes(needle);
  }), [segments, start, end, needle]);
  // Paragraphs come from every segment so a filter never moves a boundary.
  const grouped = useMemo(() => groupParagraphs(segments), [segments]);
  const asParagraphs = view === 'paragraphs';
  const filteredParagraphs = useMemo(() => {
    if (!asParagraphs) return [];
    const keep = new Set(filtered);
    return grouped
      .map((p) => ({ start: p.start, members: p.segments.filter((s) => keep.has(s)) }))
      .filter((p) => p.members.length > 0);
  }, [asParagraphs, grouped, filtered]);
  const { shown, reset, onScroll } = usePagedList(asParagraphs ? filteredParagraphs.length : filtered.length);
  const visibleParagraphs = filteredParagraphs.slice(0, shown);
  const visible = asParagraphs ? [] : filtered.slice(0, shown);

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

  // Pre-segment-storage episodes only have plain processed text, so Processed
  // falls back to it on an empty result or 404 only; any other error (e.g. 500) must still show as an error.
  const processedFallbackReady = source === 'processed'
    && ((query.isSuccess && segments.length === 0) || status === 404);
  const fallback = processedFallbackReady
    ? episode.transcript
    : original404 && transcriptQuery.isSuccess ? transcriptQuery.data : undefined;
  const fallbackPending = original404 && transcriptQuery.isLoading;
  const fallbackError = original404 && transcriptQuery.isError ? transcriptQuery.error : undefined;

  return (
    <Modal ariaLabel="Transcript" onClose={onClose} panelClassName="w-full max-w-5xl h-[90vh] flex flex-col text-foreground">
      <div className="flex items-start justify-between gap-3 p-4 border-b border-border">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">Transcript</h2>
          <p className="text-sm text-muted-foreground truncate">{episode.title}</p>
        </div>
        <button
          onClick={onClose}
          className={`${btnBase} ${btnGhost}`}
        >
          Close
        </button>
      </div>

      <div className="grid gap-4 p-4 border-b border-border">
        <div className="min-w-0">
          <label htmlFor="transcript-search" className={filterLabel}>Search</label>
          <input
            id="transcript-search"
            type="search"
            placeholder="Word or phrase"
            value={search}
            onChange={(e) => onFilter(() => setSearch(e.target.value))}
            className={`${filterControl} ${inputBase}`}
          />
        </div>
        <div className="grid grid-cols-2 min-[375px]:grid-cols-[auto_1fr_1fr] sm:grid-cols-3 gap-4">
          <div className="min-w-0 col-span-2 min-[375px]:col-span-1">
            <label htmlFor="transcript-source" className={filterLabel}>Source</label>
            <select
              id="transcript-source"
              value={source}
              onChange={(e) => onFilter(() => setSource(e.target.value as Source))}
              className={`${filterControl} ${selectBase}`}
            >
              <option value="original">Original</option>
              <option value="processed">Processed</option>
            </select>
          </div>
          <div className="min-w-0">
            <label htmlFor="transcript-start" className={filterLabel}>Start</label>
            <input
              id="transcript-start"
              aria-invalid={startBad}
              value={startText}
              placeholder="0:00"
              inputMode="numeric"
              onChange={(e) => onFilter(() => setStartText(e.target.value))}
              className={`${filterControl} ${inputBase} ${invalidField} tabular-nums`}
            />
          </div>
          <div className="min-w-0">
            <label htmlFor="transcript-end" className={filterLabel}>End</label>
            <input
              id="transcript-end"
              aria-invalid={endBad}
              value={endText}
              placeholder={lastEnd}
              inputMode="numeric"
              onChange={(e) => onFilter(() => setEndText(e.target.value))}
              className={`${filterControl} ${inputBase} ${invalidField} tabular-nums`}
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <SegmentedToggle
            variant="toolbar"
            options={VIEW_OPTIONS}
            value={view}
            onChange={(v) => { writeView(v); setView(v); reset(); }}
            ariaLabel="Transcript view"
          />
          {source === 'original' && markers.length > 0 && (
            <label className="flex items-center gap-2 min-h-11 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={highlight}
                onChange={(e) => setHighlight(e.target.checked)}
                className={focusRing}
              />
              Highlight ads
            </label>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4" onScroll={onScroll}>
        {(query.isLoading || fallbackPending) && <SkeletonRows count={6} />}
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
        {asParagraphs && visibleParagraphs.length > 0 && (
          <ol className="text-sm space-y-3">
            {visibleParagraphs.map((para, i) => {
              const ads = highlight && source === 'original'
                ? para.members.map((seg) => adFor(seg, markers))
                : [];
              const firstAd = ads.find(Boolean);
              return (
                <li key={`${para.start}-${i}`} className="flex gap-3 border-l-2 border-l-transparent pl-3">
                  <span className="shrink-0 w-16 tabular-nums text-muted-foreground">
                    {formatTimestamp(para.start)}
                  </span>
                  <span className="min-w-0 flex-1 break-words leading-relaxed">
                    {para.members.map((seg, j) => (
                      <Fragment key={`${seg.start}-${j}`}>
                        {j > 0 && ' '}
                        <span className={ads[j] ? 'bg-destructive/10 rounded-sm box-decoration-clone' : undefined}>
                          <Highlight text={seg.text.trim()} needle={needle} />
                        </span>
                      </Fragment>
                    ))}
                  </span>
                  {firstAd && (
                    <span className={`${badgeBase} shrink-0 self-start ${tint.destructive}`}>
                      {firstAd.sponsor || 'Ad'}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {!asParagraphs && visible.length > 0 && (
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
                    <span className={`${badgeBase} shrink-0 self-start ${tint.destructive}`}>
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
