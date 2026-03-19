import { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { addFeed, importOpml, OpmlImportResult, getFeeds } from '../api/feeds';
import { searchPodcasts, PodcastSearchResult } from '../api/podcastSearch';
import { getSettings } from '../api/settings';
import LoadingSpinner from '../components/LoadingSpinner';

// URL validation patterns
const URL_PATTERN = /^https?:\/\/[a-zA-Z0-9][-a-zA-Z0-9]*(\.[a-zA-Z0-9][-a-zA-Z0-9]*)+.*$/;
const RSS_EXTENSIONS = ['.xml', '.rss', '.atom', '/rss', '/feed'];

interface UrlValidation {
  isValid: boolean;
  error: string | null;
  warning: string | null;
}

function validateUrl(url: string): UrlValidation {
  if (!url.trim()) {
    return { isValid: false, error: null, warning: null };
  }

  // Check for valid URL structure
  if (!URL_PATTERN.test(url)) {
    // Check if missing protocol
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      return {
        isValid: false,
        error: 'URL must start with http:// or https://',
        warning: null
      };
    }
    return {
      isValid: false,
      error: 'Invalid URL format',
      warning: null
    };
  }

  // Check for HTTPS recommendation
  const isHttps = url.startsWith('https://');

  // Check if it looks like an RSS feed
  const looksLikeRss = RSS_EXTENSIONS.some(ext =>
    url.toLowerCase().includes(ext)
  );

  return {
    isValid: true,
    error: null,
    warning: !looksLikeRss && isHttps
      ? 'This URL may not be an RSS feed. Ensure it points to a valid podcast RSS feed.'
      : !isHttps
        ? 'Consider using HTTPS for secure connections.'
        : null
  };
}

function SearchResultItem({ result, isSubscribed, isAdding, onAdd }: {
  result: PodcastSearchResult;
  isSubscribed: boolean;
  isAdding: boolean;
  onAdd: (feedUrl: string) => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [imageError, setImageError] = useState(false);

  const handleAdd = async () => {
    setError(null);
    try {
      await onAdd(result.feedUrl);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-border hover:bg-accent/30 transition-colors">
      {result.artworkUrl && !imageError ? (
        <img
          src={result.artworkUrl}
          alt=""
          className="w-14 h-14 rounded object-cover flex-shrink-0 bg-muted"
          loading="lazy"
          onError={() => setImageError(true)}
        />
      ) : (
        <div className="w-14 h-14 rounded bg-muted flex-shrink-0 flex items-center justify-center">
          <svg className="w-6 h-6 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
          </svg>
        </div>
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <a
              href={`https://podcastindex.org/podcast/${result.id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-semibold text-foreground hover:text-primary truncate block"
              title={result.title}
            >
              {result.title}
            </a>
            {result.author && (
              <p className="text-xs text-muted-foreground truncate">{result.author}</p>
            )}
          </div>
          {isSubscribed ? (
            <span className="flex-shrink-0 text-muted-foreground" title="Already subscribed">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </span>
          ) : (
            <button
              onClick={handleAdd}
              disabled={isAdding}
              className="flex-shrink-0 p-1.5 rounded-md text-primary hover:bg-primary/10 disabled:opacity-50 transition-colors"
              title="Add this podcast"
            >
              {isAdding ? (
                <LoadingSpinner size="sm" inline />
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              )}
            </button>
          )}
        </div>
        {result.description && (
          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{result.description}</p>
        )}
        {error && (
          <p className="text-xs text-destructive mt-1">{error}</p>
        )}
      </div>
    </div>
  );
}

function AddFeed() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Input state
  const [inputValue, setInputValue] = useState('');
  const [customSlug, setCustomSlug] = useState('');
  const [autoProcessOverride, setAutoProcessOverride] = useState<boolean | null>(null);
  const [maxEpisodes, setMaxEpisodes] = useState<string>('');

  // Search state
  const [searchResults, setSearchResults] = useState<PodcastSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [addingFeedUrl, setAddingFeedUrl] = useState<string | null>(null);

  // OPML state
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [opmlResult, setOpmlResult] = useState<OpmlImportResult | null>(null);

  // Detect URL vs search
  const isUrl = /^https?:\/\//.test(inputValue);

  // URL validation (only when it looks like a URL)
  const urlValidation = useMemo(() => isUrl ? validateUrl(inputValue) : { isValid: false, error: null, warning: null }, [inputValue, isUrl]);
  const [touched, setTouched] = useState(false);

  // Settings query to check if PodcastIndex is configured
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });
  const podcastIndexConfigured = settings?.podcastIndexApiKeyConfigured ?? false;

  // Existing feeds for "already added" detection
  const { data: feedsData } = useQuery({
    queryKey: ['feeds'],
    queryFn: getFeeds,
  });
  const subscribedUrls = useMemo(() => {
    if (!feedsData) return new Set<string>();
    return new Set(feedsData.map((f) => f.sourceUrl));
  }, [feedsData]);

  // Debounced search with AbortController to cancel stale requests
  useEffect(() => {
    if (isUrl || !podcastIndexConfigured || inputValue.trim().length < 2) {
      setSearchResults([]);
      setSearchError(null);
      return;
    }

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setIsSearching(true);
      setSearchError(null);
      try {
        const results = await searchPodcasts(inputValue.trim(), controller.signal);
        setSearchResults(results);
      } catch (err) {
        if (!controller.signal.aborted) {
          setSearchError((err as Error).message);
          setSearchResults([]);
        }
      } finally {
        if (!controller.signal.aborted) setIsSearching(false);
      }
    }, 400);

    return () => { controller.abort(); clearTimeout(timer); };
  }, [inputValue, isUrl, podcastIndexConfigured]);

  // Add feed mutation (for URL submit)
  const mutation = useMutation({
    mutationFn: () => addFeed(inputValue, customSlug || undefined, autoProcessOverride, maxEpisodes ? parseInt(maxEpisodes, 10) : undefined),
    onSuccess: (feed) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    },
  });

  // Add feed from search result
  const addFromSearch = useCallback(async (feedUrl: string) => {
    setAddingFeedUrl(feedUrl);
    try {
      const feed = await addFeed(feedUrl, customSlug || undefined, autoProcessOverride, maxEpisodes ? parseInt(maxEpisodes, 10) : undefined);
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    } finally {
      setAddingFeedUrl(null);
    }
  }, [customSlug, autoProcessOverride, maxEpisodes, queryClient, navigate]);

  // OPML handlers
  const opmlMutation = useMutation({
    mutationFn: (file: File) => importOpml(file),
    onSuccess: (result) => {
      setOpmlResult(result);
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
  });

  const handleFileSelect = useCallback((file: File) => {
    const validExtensions = ['.opml', '.xml'];
    const hasValidExt = validExtensions.some(ext =>
      file.name.toLowerCase().endsWith(ext)
    );
    if (!hasValidExt) {
      alert('Please select an OPML file (.opml or .xml)');
      return;
    }
    opmlMutation.mutate(file);
  }, [opmlMutation]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  }, [handleFileSelect]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (isUrl && inputValue.trim() && urlValidation.isValid) {
      mutation.mutate();
    }
  };

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-bold text-foreground mb-6">Add New Feed</h1>

      {/* No-credentials info banner */}
      {!podcastIndexConfigured && (
        <div className="mb-6 p-4 rounded-lg bg-accent/50 border border-border">
          <p className="text-sm text-muted-foreground">
            <Link to="/settings#podcast-index" className="text-primary hover:underline font-medium">
              Configure PodcastIndex API credentials
            </Link>
            {' '}to search for podcasts by name. You can still add feeds by URL below.
          </p>
        </div>
      )}

      {/* Section A: Unified Input */}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="podcastInput" className="block text-sm font-medium text-foreground mb-2">
            {podcastIndexConfigured ? 'Search podcasts or enter RSS URL' : 'Podcast RSS Feed URL'}
          </label>
          <input
            type="text"
            id="podcastInput"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onBlur={() => { if (isUrl) setTouched(true); }}
            placeholder={podcastIndexConfigured ? 'Search by name or paste an RSS feed URL...' : 'https://example.com/podcast/feed.xml'}
            className={`w-full px-4 py-2 rounded-lg border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring ${
              isUrl && touched && urlValidation.error
                ? 'border-destructive focus:ring-destructive'
                : isUrl && touched && urlValidation.warning
                  ? 'border-yellow-500 focus:ring-yellow-500'
                  : 'border-input'
            }`}
          />
          {isUrl && touched && urlValidation.error && (
            <p className="mt-1 text-sm text-destructive">{urlValidation.error}</p>
          )}
          {isUrl && touched && !urlValidation.error && urlValidation.warning && (
            <p className="mt-1 text-sm text-yellow-600 dark:text-yellow-500">{urlValidation.warning}</p>
          )}
        </div>

        {/* Section B: Advanced Settings (collapsible) */}
        <details className="group">
          <summary className="text-sm text-primary hover:underline cursor-pointer list-none">
            Advanced options
            <span className="text-muted-foreground font-normal"> -- applies to URL and search results</span>
          </summary>
          <div className="mt-4 space-y-4">
            <div>
              <label htmlFor="slug" className="block text-sm font-medium text-foreground mb-2">
                Custom Slug (optional)
              </label>
              <input
                type="text"
                id="slug"
                value={customSlug}
                onChange={(e) => setCustomSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                placeholder="my-podcast"
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <p className="mt-1 text-sm text-muted-foreground">
                Custom URL path for this feed. Only lowercase letters, numbers, and hyphens.
              </p>
            </div>

            <div>
              <label htmlFor="autoProcess" className="block text-sm font-medium text-foreground mb-2">
                Auto-Process
              </label>
              <select
                id="autoProcess"
                value={autoProcessOverride === true ? 'enable' : autoProcessOverride === false ? 'disable' : 'global'}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value === 'enable') setAutoProcessOverride(true);
                  else if (value === 'disable') setAutoProcessOverride(false);
                  else setAutoProcessOverride(null);
                }}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="global">Global Default</option>
                <option value="enable">Enabled</option>
                <option value="disable">Disabled</option>
              </select>
              <p className="mt-1 text-sm text-muted-foreground">
                Controls whether new episodes are automatically processed.
              </p>
            </div>

            <div>
              <label htmlFor="maxEpisodes" className="block text-sm font-medium text-foreground mb-2">
                Max Episodes in Feed
              </label>
              <input
                type="number"
                id="maxEpisodes"
                value={maxEpisodes}
                onChange={(e) => setMaxEpisodes(e.target.value)}
                placeholder="300 (default)"
                min={10}
                max={500}
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <p className="mt-1 text-sm text-muted-foreground">
                Limits how many episodes are served to podcast clients. Max: 500.
              </p>
            </div>
          </div>
        </details>

        {/* URL mode: show Add Feed button */}
        {isUrl && (
          <>
            {mutation.error && (
              <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
                {(mutation.error as Error).message}
              </div>
            )}
            <div className="flex gap-4">
              <button
                type="submit"
                disabled={mutation.isPending || !inputValue.trim() || (touched && !urlValidation.isValid)}
                className="flex-1 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {mutation.isPending ? 'Adding Feed...' : 'Add Feed'}
              </button>
              <button
                type="button"
                onClick={() => navigate('/')}
                className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors"
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </form>

      {/* Section C: Search Results */}
      {!isUrl && inputValue.trim() && podcastIndexConfigured && (
        <div className="mt-4 space-y-2">
          {isSearching && (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border animate-pulse">
                  <div className="w-14 h-14 rounded bg-muted flex-shrink-0" />
                  <div className="flex-1 space-y-2">
                    <div className="h-4 bg-muted rounded w-3/4" />
                    <div className="h-3 bg-muted rounded w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {searchError && (
            <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
              {searchError}
            </div>
          )}

          {!isSearching && !searchError && searchResults.length === 0 && inputValue.trim().length >= 2 && (
            <p className="text-sm text-muted-foreground py-4 text-center">No podcasts found</p>
          )}

          {!isSearching && searchResults.map((result) => {
            const isSubscribed = subscribedUrls.has(result.feedUrl);
            const isAdding = addingFeedUrl === result.feedUrl;

            return (
              <SearchResultItem
                key={result.id}
                result={result}
                isSubscribed={isSubscribed}
                isAdding={isAdding}
                onAdd={addFromSearch}
              />
            );
          })}
        </div>
      )}

      {/* Divider */}
      <div className="relative my-8">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-border"></div>
        </div>
        <div className="relative flex justify-center text-sm">
          <span className="bg-background px-4 text-muted-foreground">or import multiple feeds</span>
        </div>
      </div>

      {/* OPML Import Section */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-foreground">Import from OPML</h2>
        <p className="text-sm text-muted-foreground">
          Upload an OPML file to import multiple podcast feeds at once
        </p>

        <div
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
            isDragging
              ? 'border-primary bg-primary/5'
              : 'border-border hover:border-primary/50'
          }`}
        >
          <input
            type="file"
            ref={fileInputRef}
            accept=".opml,.xml"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFileSelect(file);
              e.target.value = '';
            }}
            className="hidden"
          />

          {opmlMutation.isPending ? (
            <div className="space-y-2">
              <LoadingSpinner size="md" />
              <p className="text-muted-foreground">Importing feeds...</p>
            </div>
          ) : (
            <div className="space-y-4">
              <svg
                className="w-12 h-12 mx-auto text-muted-foreground"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                />
              </svg>
              <div>
                <p className="text-foreground">Drop your OPML file here, or</p>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="text-primary hover:underline font-medium"
                >
                  browse to select
                </button>
              </div>
              <p className="text-xs text-muted-foreground">Supports .opml and .xml files</p>
            </div>
          )}
        </div>

        {opmlMutation.error && (
          <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
            {(opmlMutation.error as Error).message}
          </div>
        )}

        {opmlResult && (
          <div className="p-4 rounded-lg border border-border bg-card">
            <h3 className="font-medium text-foreground mb-2">Import Results</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="text-green-600 dark:text-green-400">
                <span className="font-semibold">{opmlResult.imported}</span> feeds imported
              </div>
              {opmlResult.failed > 0 && (
                <div className="text-destructive">
                  <span className="font-semibold">{opmlResult.failed}</span> failed
                </div>
              )}
            </div>
            {opmlResult.feeds.failed.length > 0 && (
              <div className="mt-3 text-sm">
                <p className="text-muted-foreground mb-1">Failed imports:</p>
                <ul className="list-disc list-inside text-destructive space-y-1">
                  {opmlResult.feeds.failed.slice(0, 5).map((item, i) => (
                    <li key={i} className="truncate" title={item.url}>{item.error}</li>
                  ))}
                  {opmlResult.feeds.failed.length > 5 && (
                    <li className="text-muted-foreground">...and {opmlResult.feeds.failed.length - 5} more</li>
                  )}
                </ul>
              </div>
            )}
            <button
              onClick={() => setOpmlResult(null)}
              className="mt-3 text-sm text-muted-foreground hover:text-foreground"
            >
              Dismiss
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default AddFeed;
