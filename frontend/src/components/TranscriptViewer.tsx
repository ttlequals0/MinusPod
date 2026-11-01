import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getFinalSegments, getOriginalSegments, getOriginalTranscript, type OriginalSegment } from '../api/feeds';
import type { AdSegment, EpisodeDetail } from '../api/types';
import { ApiError, getErrorMessage } from '../api/client';
import { Modal } from './Modal';
import { SkeletonRows } from './Skeleton';
import { formatTimestamp } from '../utils/format';
import { parseTimeInput } from '../utils/adReviewHelpers';
import { usePagedList } from '../hooks/usePagedList';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import { useTransientState } from '../hooks/useTransientState';
import SegmentedToggle from './SegmentedToggle';
import { groupParagraphs } from '../utils/transcriptParagraphs';
import { btnBase, btnGhost, touchTarget } from './buttonStyles';
import { focusRing, inputBase, selectBase, filterLabel, filterControl } from './fieldStyles';
import { badgeBase, tint } from './badgeStyles';

type Source = 'original' | 'processed';
type View = 'paragraphs' | 'lines';

const VIEW_KEY = 'minuspod.transcript.view';
const VIEW_OPTIONS = [
  { value: 'paragraphs', label: 'Paragraphs' },
  { value: 'lines', label: 'Lines' },
] as const;

type Context = 0 | 1 | 3;
const CONTEXT_KEY = 'minuspod.transcript.context';
const CONTEXT_OPTIONS = [
  { value: 0, label: 'None' },
  { value: 1, label: '1' },
  { value: 3, label: '3' },
] as const;
const FLASH_MS = 1500;

interface Flash {
  seg: OriginalSegment;
  block: 'start' | 'center';
}

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

// Case-insensitive regex on `text` itself: toLowerCase+indexOf drifts on chars whose lowercase form changes length (e.g. Turkish 'I').
function Highlight({ text, re }: { text: string; re: RegExp | null }) {
  if (!re) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let from = 0;
  for (const m of text.matchAll(re)) {
    parts.push(text.slice(from, m.index), <mark key={m.index}>{m[0]}</mark>);
    from = m.index + m[0].length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}

const rowBg = (flashed: boolean, ad: boolean) =>
  flashed ? 'bg-primary/20' : ad ? 'bg-destructive/10' : 'bg-transparent';

const mutedClass = (seg: OriginalSegment, matchSet: Set<OriginalSegment>, neighbours: number) =>
  neighbours > 0 && !matchSet.has(seg) ? 'text-muted-foreground' : '';

interface RowProps {
  row: number;
  matchSet: Set<OriginalSegment>;
  neighbours: number;
  re: RegExp | null;
  flashed: boolean;
  // Ad markers to tint with, or null when ad highlighting is off.
  markers: AdSegment[] | null;
  onJump: ((seg: OriginalSegment) => void) | null;
}

function JumpButton({ seg, onJump, className }: { seg: OriginalSegment; onJump: (seg: OriginalSegment) => void; className: string }) {
  return (
    <button
      type="button"
      onClick={() => onJump(seg)}
      aria-label={`Show in transcript, match at ${formatTimestamp(seg.start)}`}
      className={`${btnBase} ${btnGhost} ${touchTarget} shrink-0 self-start max-sm:justify-start ${className}`}
    >
      Show in transcript
    </button>
  );
}

const ParagraphRow = memo(function ParagraphRow({
  para, row, matchSet, neighbours, re, flashed, markers, onJump,
}: RowProps & { para: { start: number; members: OriginalSegment[] } }) {
  const ads = markers ? para.members.map((seg) => adFor(seg, markers)) : [];
  const firstAd = ads.find(Boolean);
  const hit = onJump ? para.members.find((seg) => matchSet.has(seg)) : undefined;
  return (
    <li
      data-row={row}
      className={`flex flex-wrap sm:flex-nowrap gap-3 border-l-2 border-l-transparent pl-3 transition-colors duration-500 ${rowBg(flashed, false)}`}
    >
      <span className="shrink-0 w-16 tabular-nums text-muted-foreground">
        {formatTimestamp(para.start)}
      </span>
      <span className="min-w-0 flex-1 break-words leading-relaxed">
        {para.members.map((seg, j) => (
          <Fragment key={`${seg.start}-${j}`}>
            {j > 0 && ' '}
            <span
              className={[
                ads[j] ? 'bg-destructive/10 rounded-sm box-decoration-clone' : '',
                mutedClass(seg, matchSet, neighbours),
              ].filter(Boolean).join(' ') || undefined}
            >
              <Highlight text={seg.text.trim()} re={re} />
            </span>
          </Fragment>
        ))}
      </span>
      {firstAd && (
        <span className={`${badgeBase} ${tint.destructive} shrink-0 self-start`}>
          {firstAd.sponsor || 'Ad'}
        </span>
      )}
      {hit && onJump && <JumpButton seg={hit} onJump={onJump} className="max-sm:ml-[4.75rem] max-sm:basis-[calc(100%-4.75rem)]" />}
    </li>
  );
});

const LineRow = memo(function LineRow({
  seg, row, matchSet, neighbours, re, flashed, markers, onJump,
}: RowProps & { seg: OriginalSegment }) {
  const ad = markers ? adFor(seg, markers) : undefined;
  return (
    <li
      data-row={row}
      className={`flex flex-wrap sm:flex-nowrap gap-3 border-l-2 pl-3 py-1 transition-colors duration-500 ${
        ad ? 'border-l-destructive' : 'border-l-transparent'
      } ${rowBg(flashed, !!ad)}`}
    >
      <span className="shrink-0 w-28 tabular-nums text-muted-foreground">
        {formatTimestamp(seg.start)} - {formatTimestamp(seg.end)}
      </span>
      <span className={`min-w-0 flex-1 whitespace-pre-wrap break-words ${mutedClass(seg, matchSet, neighbours)}`}>
        <Highlight text={seg.text} re={re} />
      </span>
      {ad && (
        <span className={`${badgeBase} ${tint.destructive} shrink-0 self-start`}>
          {ad.sponsor || 'Ad'}
        </span>
      )}
      {onJump && matchSet.has(seg) && <JumpButton seg={seg} onJump={onJump} className="max-sm:basis-full" />}
    </li>
  );
});

function TranscriptViewer({ slug, episodeId, episode, onClose }: Props) {
  const [source, setSource] = useState<Source>(
    episode.originalTranscriptAvailable ? 'original' : 'processed',
  );
  const [search, setSearch] = useState('');
  const [startText, setStartText] = useState('');
  const [endText, setEndText] = useState('');
  const [highlight, setHighlight] = useState(false);
  const [view, setView] = useState<View>(readView);
  const [context, setContext] = useLocalStorageState<Context>(CONTEXT_KEY, 0);
  const [showAll, setShowAll] = useState(false);
  const [cursorSeg, setCursorSeg] = useState<OriginalSegment | null>(null);
  const [flash, setFlash] = useTransientState<Flash | null>(null, FLASH_MS);
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);
  const scrolledFor = useRef<Flash | null>(null);

  const query = useQuery({
    queryKey: [source === 'original' ? 'originalSegments' : 'finalSegments', slug, episodeId],
    queryFn: () => (source === 'original' ? getOriginalSegments : getFinalSegments)(slug, episodeId),
  });
  const segments = useMemo(() => query.data?.segments ?? [], [query.data]);
  const markers = useMemo(() => episode.adMarkers ?? [], [episode.adMarkers]);

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
  const re = useMemo(() => (needle ? new RegExp(escapeRegExp(needle), 'gi') : null), [needle]);
  // The full window is shown: no search term, or after jumping to a match.
  const browsing = !needle || showAll;

  const windowed = useMemo(() => segments.filter((seg) => {
    if (start !== null && seg.end <= start) return false;
    return end === null || seg.start < end;
  }), [segments, start, end]);
  const matchSet = useMemo(
    () => new Set(needle ? windowed.filter((seg) => seg.text.toLowerCase().includes(needle)) : []),
    [windowed, needle],
  );
  const neighbours = browsing ? 0 : context;
  // Matches plus `neighbours` segments each side, in one pass with a watermark.
  const filtered = useMemo(() => {
    if (browsing) return windowed;
    const out: OriginalSegment[] = [];
    let next = 0;
    windowed.forEach((seg, i) => {
      if (!matchSet.has(seg)) return;
      const hi = Math.min(windowed.length - 1, i + neighbours);
      for (let k = Math.max(next, i - neighbours); k <= hi; k++) out.push(windowed[k]);
      next = Math.max(next, hi + 1);
    });
    return out;
  }, [windowed, matchSet, browsing, neighbours]);
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
  // One entry per rendered row, so Next always moves the view.
  const matchList = useMemo(() => {
    if (!asParagraphs) return filtered.filter((seg) => matchSet.has(seg));
    return filteredParagraphs.flatMap((p) => p.members.find((seg) => matchSet.has(seg)) ?? []);
  }, [asParagraphs, filtered, filteredParagraphs, matchSet]);
  const matchIndex = useMemo(() => {
    const map = new Map<OriginalSegment, number>();
    if (!asParagraphs) {
      matchList.forEach((seg, i) => map.set(seg, i));
    } else {
      let i = 0;
      filteredParagraphs.forEach((p) => {
        const hits = p.members.filter((seg) => matchSet.has(seg));
        if (hits.length === 0) return;
        hits.forEach((seg) => map.set(seg, i));
        i++;
      });
    }
    return map;
  }, [asParagraphs, matchList, filteredParagraphs, matchSet]);
  const cursor = cursorSeg ? matchIndex.get(cursorSeg) : undefined;
  const { shown, reset, reveal, onScroll } = usePagedList(asParagraphs ? filteredParagraphs.length : filtered.length);
  const visibleParagraphs = filteredParagraphs.slice(0, shown);
  const visible = asParagraphs ? [] : filtered.slice(0, shown);
  // Row position of each segment in the rendered list.
  const posBySeg = useMemo(() => {
    const map = new Map<OriginalSegment, number>();
    if (asParagraphs) filteredParagraphs.forEach((p, i) => p.members.forEach((seg) => map.set(seg, i)));
    else filtered.forEach((seg, i) => map.set(seg, i));
    return map;
  }, [asParagraphs, filteredParagraphs, filtered]);
  const flashedPos = flash ? posBySeg.get(flash.seg) : undefined;

  const showInTranscript = useCallback((seg: OriginalSegment) => {
    setShowAll(true);
    setCursorSeg(seg);
    setFlash({ seg, block: 'start' });
  }, [setFlash]);
  const step = (dir: 1 | -1) => {
    if (matchList.length === 0) return;
    const next = cursor === undefined
      ? (dir === 1 ? 0 : matchList.length - 1)
      : (cursor + dir + matchList.length) % matchList.length;
    setCursorSeg(matchList[next]);
    setFlash({ seg: matchList[next], block: 'center' });
  };

  // Grow the window to the target row, then scroll once it has rendered.
  useEffect(() => {
    if (!flash || scrolledFor.current === flash) return;
    const pos = posBySeg.get(flash.seg);
    if (pos === undefined) return;
    reveal(pos);
    const row = listRef.current?.querySelector(`[data-row="${pos}"]`);
    if (!row) return;
    scrolledFor.current = flash;
    row.scrollIntoView({ block: flash.block });
  }, [flash, shown, posBySeg, reveal]);
  // The clicked "Show in transcript" button unmounts, so hand focus to the way back.
  useEffect(() => {
    if (showAll) backRef.current?.focus();
  }, [showAll]);

  const backToMatches = () => {
    setShowAll(false);
    setCursorSeg(null);
    reset();
    if (listRef.current) listRef.current.scrollTop = 0;
    searchRef.current?.focus();
  };
  const onJump = browsing ? null : showInTranscript;
  const rowProps = { matchSet, neighbours, re, onJump, markers: highlight && source === 'original' ? markers : null };

  const onFilter = (apply: () => void) => {
    apply();
    reset();
    setShowAll(false);
    setCursorSeg(null);
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
            ref={searchRef}
            type="search"
            placeholder="Word or phrase"
            value={search}
            onChange={(e) => onFilter(() => setSearch(e.target.value))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && needle) {
                e.preventDefault();
                step(1);
              }
            }}
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
        <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
          <div>
            <span className={filterLabel}>View</span>
            <SegmentedToggle
              variant="toolbar"
              options={VIEW_OPTIONS}
              value={view}
              onChange={(v) => { writeView(v); setView(v); reset(); }}
              ariaLabel="Transcript view"
            />
          </div>
          <div aria-describedby={needle ? undefined : 'transcript-context-hint'}>
            <span className={filterLabel}>Context</span>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <SegmentedToggle
                variant="toolbar"
                options={CONTEXT_OPTIONS}
                value={context}
                onChange={(c) => { setContext(c); if (!showAll) reset(); }}
                ariaLabel="Context segments"
                disabled={!needle}
              />
              {!needle && <span id="transcript-context-hint" className="text-xs text-muted-foreground whitespace-nowrap">Needs a search term</span>}
            </div>
          </div>
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

      <div ref={listRef} className="flex-1 overflow-y-auto p-4" onScroll={onScroll}>
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
            <Highlight text={fallback} re={re} />
          </pre>
        )}
        {segments.length > 0 && filtered.length === 0 && (
          <p className="text-sm text-muted-foreground">No segments match the current filters.</p>
        )}
        {asParagraphs && visibleParagraphs.length > 0 && (
          <ol className="text-sm space-y-3">
            {visibleParagraphs.map((para, i) => (
              <ParagraphRow key={`${para.start}-${i}`} para={para} row={i} flashed={i === flashedPos} {...rowProps} />
            ))}
          </ol>
        )}
        {!asParagraphs && visible.length > 0 && (
          <ol className="text-sm space-y-px">
            {visible.map((seg, i) => (
              <LineRow key={`${seg.start}-${i}`} seg={seg} row={i} flashed={i === flashedPos} {...rowProps} />
            ))}
          </ol>
        )}
      </div>

      <div className="px-4 py-2 sm:py-4 border-t border-border text-sm text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1">
        {segments.length > 0 && (
          <span>
            {needle
              ? `${matchSet.size} ${matchSet.size === 1 ? 'match' : 'matches'} in ${windowed.length} segments`
              : `${filtered.length} of ${segments.length} segments`}
          </span>
        )}
        {fallback && 'Plain text, no timestamps'}
        {needle && showAll && (
          <button
            ref={backRef}
            type="button"
            onClick={backToMatches}
            className={`${touchTarget} px-1 rounded underline text-primary ${focusRing}`}
          >
            Back to matches
          </button>
        )}
        {needle && matchList.length > 0 && (
          <span className="flex items-center gap-2 sm:ml-auto">
            <span role="status" className="tabular-nums">
              {cursor !== undefined && `Match ${cursor + 1} of ${matchList.length}`}
            </span>
            <button type="button" onClick={() => step(-1)} className={`${btnBase} ${btnGhost} ${touchTarget}`}>
              Previous
            </button>
            <button type="button" onClick={() => step(1)} className={`${btnBase} ${btnGhost} ${touchTarget}`}>
              Next
            </button>
          </span>
        )}
      </div>
    </Modal>
  );
}

export default TranscriptViewer;
