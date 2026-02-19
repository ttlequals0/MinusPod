import { useState, useEffect, useMemo, ReactNode } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { search, rebuildSearchIndex, getSearchStats, SearchResult } from '../api/search';
import LoadingSpinner from '../components/LoadingSpinner';

type FilterType = 'all' | 'episode' | 'podcast' | 'pattern' | 'sponsor';

/**
 * Safely render a search snippet with <mark> highlights as React elements.
 * All other content is rendered as plain text (auto-escaped by React).
 */
function renderSnippet(snippet: string): ReactNode[] {
  const parts = snippet.split(/(<mark>.*?<\/mark>)/g);
  return parts.map((part, i) => {
    const match = part.match(/^<mark>(.*?)<\/mark>$/);
    if (match) {
      return <mark key={i}>{match[1]}</mark>;
    }
    return part;
  });
}

function Search() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const initialQuery = searchParams.get('q') || '';
  const initialType = (searchParams.get('type') as FilterType) || 'all';

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
    queryKey: ['search', debouncedQuery, filterType],
    queryFn: () => search(debouncedQuery, filterType === 'all' ? undefined : filterType),
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

  // Group results by type
  const groupedResults = useMemo(() => {
    if (!results?.results) return {};
    return results.results.reduce((acc, result) => {
      if (!acc[result.type]) acc[result.type] = [];
      acc[result.type].push(result);
      return acc;
    }, {} as Record<string, SearchResult[]>);
  }, [results]);

  const getResultLink = (result: SearchResult): string => {
    switch (result.type) {
      case 'episode':
        return `/feeds/${result.podcastSlug}/episodes/${result.id}`;
      case 'podcast':
        return `/feeds/${result.podcastSlug}`;
      case 'pattern':
        return '/patterns';
      case 'sponsor':
        return '/patterns';
      default:
        return '/';
    }
  };

  const typeLabels: Record<string, string> = {
    episode: 'Episodes',
    podcast: 'Podcasts',
    pattern: 'Patterns',
    sponsor: 'Sponsors',
  };

  const typeIcons: Record<string, React.ReactNode> = {
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

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Search</h1>
          <p className="text-muted-foreground mt-1">
            Search across episodes, podcasts, patterns, and sponsors
          </p>
        </div>
        <button
          onClick={() => rebuildMutation.mutate()}
          disabled={rebuildMutation.isPending}
          className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
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
          className="w-full pl-12 pr-4 py-3 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring text-lg"
        />
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-2 mb-6 overflow-x-auto pb-2">
        {(['all', 'episode', 'podcast', 'pattern', 'sponsor'] as FilterType[]).map((type) => (
          <button
            key={type}
            onClick={() => setFilterType(type)}
            className={`px-4 py-2 rounded-full text-sm font-medium transition-colors whitespace-nowrap ${
              filterType === type
                ? 'bg-primary text-primary-foreground'
                : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
            }`}
          >
            {type === 'all' ? 'All' : typeLabels[type]}
            {stats?.stats && type !== 'all' && stats.stats[type] !== undefined && (
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
            {results.total} result{results.total !== 1 ? 's' : ''} for "{results.query}"
          </p>

          {results.total === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p>No results found</p>
              <p className="text-sm mt-2">Try different keywords or rebuild the search index</p>
            </div>
          ) : filterType === 'all' ? (
            // Grouped view
            <div className="space-y-8">
              {Object.entries(groupedResults).map(([type, items]) => (
                <div key={type}>
                  <h2 className="text-lg font-semibold text-foreground mb-3 flex items-center gap-2">
                    {typeIcons[type]}
                    {typeLabels[type]}
                    <span className="text-sm font-normal text-muted-foreground">({items.length})</span>
                  </h2>
                  <div className="space-y-2">
                    {items.slice(0, 5).map((result) => (
                      <SearchResultCard key={`${result.type}-${result.id}`} result={result} link={getResultLink(result)} />
                    ))}
                    {items.length > 5 && (
                      <button
                        onClick={() => setFilterType(type as FilterType)}
                        className="text-sm text-primary hover:underline"
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
              {results.results.map((result) => (
                <SearchResultCard key={`${result.type}-${result.id}`} result={result} link={getResultLink(result)} />
              ))}
            </div>
          )}
        </>
      )}

      {!debouncedQuery && (
        <div className="text-center py-12 text-muted-foreground">
          <p>Enter a search term to find content</p>
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

function SearchResultCard({ result, link }: { result: SearchResult; link: string }) {
  return (
    <Link
      to={link}
      className="block p-4 rounded-lg border border-border bg-card hover:bg-accent/50 transition-colors"
    >
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 mt-1 text-muted-foreground">
          {result.type === 'episode' && (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
            </svg>
          )}
          {result.type === 'podcast' && (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          )}
          {(result.type === 'pattern' || result.type === 'sponsor') && (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
            </svg>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-medium text-foreground truncate">{result.title}</h3>
          {result.podcastSlug && result.type === 'episode' && (
            <p className="text-xs text-muted-foreground truncate">{result.podcastSlug}</p>
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
