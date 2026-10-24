import { useState, useEffect, useMemo } from 'react';
import { useSearchParams, Link } from 'react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { search, rebuildSearchIndex, getSearchStats } from '../api/search';
import { SkeletonRows } from '../components/Skeleton';
import { Volume2, Mic, MessageSquare, Paintbrush, CircleDollarSign, Search as SearchIcon } from 'lucide-react';
import PageTabs from '../components/PageTabs';
import StatusBadge from '../components/StatusBadge';
import { renderSnippet } from '../utils/searchSnippet';
import { formatDate } from '../utils/format';
import QueryError from '../components/QueryError';
import { btnBase, btnSecondary, btnGhost } from '../components/buttonStyles';
import { focusRing, inputBase, labelBase } from '../components/fieldStyles';
import { pageHeader, pageTitle, cardTitle, pageDescription } from '../components/pageStyles';

type FilterType = 'all' | 'episode' | 'podcast' | 'transcript' | 'pattern' | 'sponsor';
type ResultType = Exclude<FilterType, 'all'>;

const FILTER_TYPES: FilterType[] = ['all', 'episode', 'podcast', 'transcript', 'pattern', 'sponsor'];

function isFilterType(value: string | null): value is FilterType {
  return value !== null && (FILTER_TYPES as string[]).includes(value);
}

interface NormalizedResult {
  type: ResultType;
  id: string;
  title: string;
  subtitle?: string;
  snippet: string | null;
  link: string;
  titleSnippet?: string | null;
  status?: string;
  date?: string;
}

const typeIcons: Record<ResultType, React.ReactNode> = {
  episode: <Volume2 className="w-4 h-4" aria-hidden="true" />,
  podcast: <Mic className="w-4 h-4" aria-hidden="true" />,
  transcript: <MessageSquare className="w-4 h-4" aria-hidden="true" />,
  pattern: <Paintbrush className="w-4 h-4" aria-hidden="true" />,
  sponsor: <CircleDollarSign className="w-4 h-4" aria-hidden="true" />,
};

function Search() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const initialQuery = searchParams.get('q') || '';
  const typeParam = searchParams.get('type');
  const initialType: FilterType = isFilterType(typeParam) ? typeParam : 'all';

  const [query, setQuery] = useState(initialQuery);
  const [debouncedQuery, setDebouncedQuery] = useState(initialQuery);
  const [filterType, setFilterType] = useState<FilterType>(initialType);

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(query);
      if (query) {
        setSearchParams({ q: query, ...(filterType !== 'all' && { type: filterType }) });
      } else {
        setSearchParams({});
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query, filterType, setSearchParams]);

  const { data: results, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['search', debouncedQuery],
    queryFn: () => search(debouncedQuery),
    enabled: debouncedQuery.length >= 2,
  });

  const { data: stats } = useQuery({
    queryKey: ['searchStats'],
    queryFn: getSearchStats,
  });

  const rebuildMutation = useMutation({
    mutationFn: rebuildSearchIndex,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['searchStats'] });
      queryClient.invalidateQueries({ queryKey: ['search'] });
    },
  });

  // One group per result type, built from the endpoint's five independent lists.
  const groupedResults = useMemo<Record<ResultType, NormalizedResult[]>>(() => {
    if (!results) return { podcast: [], episode: [], transcript: [], pattern: [], sponsor: [] };
    return {
      podcast: results.shows.map((r) => ({
        type: 'podcast', id: r.slug, title: r.title, titleSnippet: r.titleSnippet, snippet: r.snippet,
        link: `/feeds/${r.slug}`,
      })),
      episode: results.episodes.map((r) => ({
        type: 'episode', id: `${r.feedSlug}-${r.episodeId}`, title: r.title,
        subtitle: r.feedTitle, date: r.publishDate ? formatDate(r.publishDate) : undefined,
        status: r.status, titleSnippet: r.titleSnippet, snippet: r.snippet,
        link: `/feeds/${r.feedSlug}/episodes/${r.episodeId}`,
      })),
      transcript: results.transcripts.map((r) => ({
        type: 'transcript', id: `${r.feedSlug}-${r.episodeId}`, title: r.title,
        snippet: r.snippet, link: `/feeds/${r.feedSlug}/episodes/${r.episodeId}`,
      })),
      pattern: results.patterns.map((r) => ({
        type: 'pattern', id: r.id, title: r.sponsor, subtitle: r.scope, snippet: r.snippet, link: `/patterns?q=${encodeURIComponent(r.sponsor)}`,
      })),
      sponsor: results.sponsors.map((r) => ({
        type: 'sponsor', id: r.id, title: r.name, snippet: r.snippet, link: `/sponsors?q=${encodeURIComponent(r.name)}`,
      })),
    };
  }, [results]);

  const total = useMemo(
    () => Object.values(groupedResults).reduce((sum, items) => sum + items.length, 0),
    [groupedResults]
  );

  const typeLabels: Record<ResultType, string> = {
    episode: 'Episodes',
    podcast: 'Podcasts',
    transcript: 'Transcripts',
    pattern: 'Patterns',
    sponsor: 'Sponsors',
  };

  return (
    <div className="max-w-3xl mx-auto">
      <div className={pageHeader}>
        <div>
          <h1 className={pageTitle}>Search</h1>
          <p className={pageDescription}>
            Search across shows, episodes, transcripts, patterns, and sponsors
          </p>
        </div>
      </div>

      {/* Search Input */}
      <label htmlFor="library-search" className={labelBase}>Search your library</label>
      <div className="relative mb-6">
        <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground" aria-hidden="true" />
        <input
          id="library-search"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search transcripts, titles, patterns..."
          autoFocus
          className={`${inputBase} w-full pl-11`}
        />
      </div>

      <PageTabs
        id="search"
        label="Result type"
        wrap
        active={filterType}
        onChange={setFilterType}
        tabs={FILTER_TYPES.map((type) => ({
          value: type,
          label: type === 'all' ? 'All' : typeLabels[type],
          badge: stats?.stats && type !== 'all' && type !== 'transcript' && stats.stats[type] !== undefined
            ? <span className="ml-1">({stats.stats[type]})</span> : undefined,
        }))}
      />

      {/* Results */}
      {isLoading && debouncedQuery.length >= 2 && <SkeletonRows count={5} />}

      {error && (
        <QueryError title="Search is unavailable." onRetry={() => void refetch()} retrying={isFetching} />
      )}

      {!isLoading && !error && debouncedQuery.length >= 2 && results && (
        <>
          <p className="text-sm text-muted-foreground mb-4">
            {total} result{total !== 1 ? 's' : ''} for "{results.query}"
          </p>

          {total === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p>No results for "{results.query}".</p>
              <p className="text-sm mt-2">Try different keywords or rebuild the search index.</p>
            </div>
          ) : filterType === 'all' ? (
            // Grouped view
            <div className="space-y-8">
              {(Object.entries(groupedResults) as [ResultType, NormalizedResult[]][])
                .filter(([, items]) => items.length > 0)
                .map(([type, items]) => (
                  <div key={type}>
                    <h2 className={`${cardTitle} mb-3 flex items-center gap-2`}>
                      {typeIcons[type]}
                      {typeLabels[type]}
                      <span className="text-sm font-normal text-muted-foreground">({items.length})</span>
                    </h2>
                    <div className="space-y-2">
                      {items.slice(0, 5).map((result) => (
                        <SearchResultCard key={`${result.type}-${result.id}`} result={result} />
                      ))}
                      {items.length > 5 && (
                        <button
                          onClick={() => setFilterType(type)}
                          className={`${btnBase} ${btnGhost}`}
                        >
                          View all {items.length} {typeLabels[type].toLowerCase()}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          ) : (
            // Flat view for filtered type
            <div className="space-y-2">
              {groupedResults[filterType].map((result) => (
                <SearchResultCard key={`${result.type}-${result.id}`} result={result} />
              ))}
            </div>
          )}
        </>
      )}

      {!debouncedQuery && (
        <div className="text-center py-12 text-muted-foreground">
          <p>Enter a search term.</p>
          {stats?.stats && (
            <p className="text-sm mt-2">
              {stats.stats.total || 0} items indexed.
            </p>
          )}
        </div>
      )}

      {debouncedQuery && debouncedQuery.length < 2 && (
        <div className="text-center py-12 text-muted-foreground">
          <p>Enter at least 2 characters to search.</p>
        </div>
      )}
      <details className="mt-8 border-t border-border pt-4 text-sm">
        <summary className={`cursor-pointer text-muted-foreground py-3 ${focusRing}`}>Search maintenance</summary>
        <button type="button" onClick={() => rebuildMutation.mutate()} disabled={rebuildMutation.isPending}
          className={`${btnBase} ${btnSecondary}`}>
          {rebuildMutation.isPending ? 'Rebuilding...' : 'Rebuild index'}
        </button>
        {rebuildMutation.isSuccess && <p role="status" className="mt-2">Search index rebuilt.</p>}
        {rebuildMutation.isError && <p role="alert" className="mt-2 text-destructive">Could not rebuild the index. Try again.</p>}
      </details>
    </div>
  );
}

function SearchResultCard({ result }: { result: NormalizedResult }) {
  return (
    <Link
      to={result.link}
      className={`block p-4 rounded-lg border border-border bg-card hover:bg-accent/50 transition-colors ${focusRing}`}
    >
      <div className="flex items-start gap-3">
        <div className="shrink-0 mt-1 text-muted-foreground">{typeIcons[result.type]}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-medium text-foreground truncate">
              {result.titleSnippet ? renderSnippet(result.titleSnippet) : result.title}
            </h3>
            {result.status && <span className="shrink-0"><StatusBadge status={result.status} /></span>}
          </div>
          {(result.subtitle || result.date) && (
            <p className="flex justify-between gap-3 text-xs text-muted-foreground">
              <span className="truncate">{result.subtitle}</span>
              {result.date && <span className="shrink-0 tabular-nums">{result.date}</span>}
            </p>
          )}
          {result.snippet && (
            <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
              {renderSnippet(result.snippet)}
            </p>
          )}
        </div>
      </div>
    </Link>
  );
}

export default Search;
