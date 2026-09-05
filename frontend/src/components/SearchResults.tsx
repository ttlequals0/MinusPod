import { Fragment, useMemo } from 'react';
import type { SearchEpisodeResult, SearchShowResult, SearchTranscriptResult } from '../api/search';
import { EPISODE_STATUS_COLORS, EPISODE_STATUS_LABELS } from '../utils/episodeStatus';
import { formatDate, formatTimestamp } from '../utils/format';
import { renderSnippet } from '../utils/searchSnippet';

export type SearchResultGroup = 'shows' | 'episodes' | 'transcripts';

// The unified endpoint also returns patterns/sponsors; those stay Advanced-only
// and are deliberately not part of this shape.
export interface UnifiedSearchGroups {
  shows: SearchShowResult[];
  episodes: SearchEpisodeResult[];
  transcripts: SearchTranscriptResult[];
}

export interface SearchResultRow {
  key: string;
  group: SearchResultGroup;
  title: string;
  subtitle?: string;
  status?: string;
  snippet?: string | null;
  timestamp?: number | null;
  to: string;
}

const GROUP_LABELS: Record<SearchResultGroup, string> = {
  shows: 'Shows',
  episodes: 'Episodes',
  transcripts: 'In transcripts',
};

// Single row order for both rendering here and caller-side keyboard indexing
// (callers index into rows built by this same function to clamp/select).
export function buildSearchResultRows(results: UnifiedSearchGroups): SearchResultRow[] {
  return [
    ...results.shows.map((r) => ({
      key: `show-${r.slug}`, group: 'shows' as const, title: r.title, snippet: r.snippet,
      to: `/feeds/${r.slug}`,
    })),
    ...results.episodes.map((r) => ({
      key: `episode-${r.feedSlug}-${r.episodeId}`, group: 'episodes' as const, title: r.title,
      subtitle: r.publishDate ? `${r.feedTitle} · ${formatDate(r.publishDate)}` : r.feedTitle,
      status: r.status, snippet: r.snippet, to: `/feeds/${r.feedSlug}/episodes/${r.episodeId}`,
    })),
    ...results.transcripts.map((r, i) => ({
      key: `transcript-${r.feedSlug}-${r.episodeId}-${i}`, group: 'transcripts' as const, title: r.title,
      snippet: r.snippet, timestamp: r.timestamp, to: `/feeds/${r.feedSlug}/episodes/${r.episodeId}`,
    })),
  ];
}

interface SearchResultsProps {
  results: UnifiedSearchGroups;
  activeIndex: number;
  // Mouse move onto a row: caller sets it active, matching the palette's existing model.
  onHover: (index: number) => void;
  // Click (or caller-driven Enter) on a row: caller decides navigation/closing.
  onSelect: (row: SearchResultRow) => void;
  // Whether the query is long enough to have searched; drives the two empty-state messages.
  ready?: boolean;
}

const HINT_MESSAGE = 'Type two or more characters to search shows, episodes and transcripts.';
const NO_MATCHES_MESSAGE = 'No shows, episodes or transcripts match.';

// Renders Shows/Episodes/In transcripts as <li role="option"> rows (plus
// role="presentation" group headers and the two shared empty-state messages),
// meant to sit inside a caller-supplied role="listbox". No layout container of its own.
function SearchResults({ results, activeIndex, onHover, onSelect, ready = true }: SearchResultsProps) {
  const rows = useMemo(() => buildSearchResultRows(results), [results]);

  if (!ready) {
    return <li role="presentation" className="px-4 py-3 text-sm text-muted-foreground">{HINT_MESSAGE}</li>;
  }
  if (rows.length === 0) {
    return <li role="presentation" className="px-4 py-3 text-sm text-muted-foreground">{NO_MATCHES_MESSAGE}</li>;
  }

  return (
    <>
      {rows.map((row, i) => {
        const header = i === 0 || rows[i - 1].group !== row.group;
        const isActive = i === activeIndex;
        return (
          <Fragment key={row.key}>
            {header && (
              <li role="presentation" className="px-4 pb-1 pt-2 text-xs font-medium text-muted-foreground">
                {GROUP_LABELS[row.group]}
              </li>
            )}
            <li
              id={row.key}
              role="option"
              aria-selected={isActive}
              onMouseMove={() => onHover(i)}
              onClick={() => onSelect(row)}
              className={`cursor-pointer border-l-2 px-4 py-2 ${isActive ? 'border-primary bg-accent font-medium text-accent-foreground' : 'border-transparent'}`}
            >
              <div className="flex items-start gap-2">
                {row.timestamp != null && (
                  <span data-testid="timestamp" className="mt-0.5 shrink-0 font-mono text-xs text-muted-foreground">
                    {formatTimestamp(row.timestamp)}
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm">{row.title}</span>
                    {row.status && (
                      <span className={`whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-normal ${EPISODE_STATUS_COLORS[row.status] || 'bg-muted text-muted-foreground'}`}>
                        {EPISODE_STATUS_LABELS[row.status] || row.status}
                      </span>
                    )}
                  </div>
                  {row.subtitle && (
                    <div className="truncate text-xs font-normal text-muted-foreground">{row.subtitle}</div>
                  )}
                  {row.snippet && (
                    <div className="mt-0.5 text-xs text-muted-foreground">{renderSnippet(row.snippet)}</div>
                  )}
                </div>
              </div>
            </li>
          </Fragment>
        );
      })}
    </>
  );
}

export default SearchResults;
