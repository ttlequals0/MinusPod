import { useState, useEffect, useMemo } from 'react';
import { useSearchParams, Link } from 'react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { search, rebuildSearchIndex, getSearchStats } from '../api/search';
import LoadingSpinner from '../components/LoadingSpinner';
import { btnSecondary } from '../components/buttonStyles';
import { focusRing } from '../components/fieldStyles';
import { renderSnippet } from '../utils/searchSnippet';

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
}

const typeIcons: Record<ResultType, React.ReactNode> = {
  episode: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
    </svg>
  ),
  podcast: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
    </svg>
  ),
  transcript: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h8M8 14h5M21 12c0 4.418-4.03 8-9 8a9.86 9.86 0 01-4-.8L3 20l1.3-3.9A7.97 7.97 0 013 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
    </svg>
  ),
  pattern: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
    </svg>
  ),
  sponsor: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
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

  const { data: results, isLoading, error } = useQuery({
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
        type: 'podcast', id: r.slug, title: r.title, snippet: r.snippet, link: `/feeds/${r.slug}`,
      })),
      episode: results.episodes.map((r) => ({
        type: 'episode', id: `${r.feedSlug}-${r.episodeId}`, title: r.title, subtitle: r.feedTitle,
        snippet: r.snippet, link: `/feeds/${r.feedSlug}/episodes/${r.episodeId}`,
      })),
      transcript: results.transcripts.map((r) => ({
        type: 'transcript', id: `${r.feedSlug}-${r.episodeId}`, title: r.title,
        snippet: r.snippet, link: `/feeds/${r.feedSlug}/episodes/${r.episodeId}`,
      })),
      pattern: results.patterns.map((r) => ({
        type: 'pattern', id: r.id, title: r.sponsor, subtitle: r.scope, snippet: r.snippet, link: '/patterns',
      })),
      sponsor: results.sponsors.map((r) => ({
        type: 'sponsor', id: r.id, title: r.name, snippet: r.snippet, link: '/patterns',
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
    <div className="max-w-4xl mx-auto">
      <div className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Search</h1>
          <p className="text-muted-foreground mt-1">
            Search across shows, episodes, transcripts, patterns, and sponsors
          </p>
        </div>
        <button
          onClick={() => rebuildMutation.mutate()}
          disabled={rebuildMutation.isPending}
          className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          {rebuildMutation.isPending ? 'Rebuilding...' : 'Rebuild Index'}
        </button>
      </div>

      {/* Search Input */}
      <div className="relative mb-6">
        <svg
          className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
          />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search transcripts, titles, patterns..."
          autoFocus
          className="w-full pl-12 pr-4 py-3 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-lg"
        />
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-2 mb-6 overflow-x-auto pb-2">
        {FILTER_TYPES.map((type) => (
          <button
            key={type}
            onClick={() => setFilterType(type)}
            className={`px-4 py-2 rounded text-sm font-medium transition-colors whitespace-nowrap ${
              filterType === type
                ? 'bg-primary text-primary-foreground'
                : btnSecondary
            } ${focusRing}`}
          >
            {type === 'all' ? 'All' : typeLabels[type]}
            {stats?.stats && type !== 'all' && type !== 'transcript' && stats.stats[type] !== undefined && (
              <span className="ml-1 opacity-70">({stats.stats[type]})</span>
            )}
          </button>
        ))}
      </div>

      {/* Results */}
      {isLoading && debouncedQuery.length >= 2 && <LoadingSpinner className="py-12" />}

      {error && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
          {(error as Error).message}
        </div>
      )}

      {!isLoading && debouncedQuery.length >= 2 && results && (
        <>
          <p className="text-sm text-muted-foreground mb-4">
            {total} result{total !== 1 ? 's' : ''} for "{results.query}"
          </p>

          {total === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p>No results found</p>
              <p className="text-sm mt-2">Try different keywords or rebuild the search index</p>
            </div>
          ) : filterType === 'all' ? (
            // Grouped view
            <div className="space-y-8">
              {(Object.entries(groupedResults) as [ResultType, NormalizedResult[]][])
                .filter(([, items]) => items.length > 0)
                .map(([type, items]) => (
                  <div key={type}>
                    <h2 className="text-lg font-semibold text-foreground mb-3 flex items-center gap-2">
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
                          className={`text-sm text-primary hover:underline ${focusRing}`}
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
          <p>Enter a search term</p>
          {stats?.stats && (
            <p className="text-sm mt-2">
              {stats.stats.total || 0} items indexed
            </p>
          )}
        </div>
      )}

      {debouncedQuery && debouncedQuery.length < 2 && (
        <div className="text-center py-12 text-muted-foreground">
          <p>Enter at least 2 characters to search</p>
        </div>
      )}
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
          <h3 className="font-medium text-foreground truncate">{result.title}</h3>
          {result.subtitle && (
            <p className="text-xs text-muted-foreground truncate">{result.subtitle}</p>
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
