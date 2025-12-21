import { useState, useMemo, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { addFeed, importOpml, OpmlImportResult } from '../api/feeds';

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

function AddFeed() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [sourceUrl, setSourceUrl] = useState('');
  const [customSlug, setCustomSlug] = useState('');
  const [showSlug, setShowSlug] = useState(false);
  const [touched, setTouched] = useState(false);

  // OPML import state
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [opmlResult, setOpmlResult] = useState<OpmlImportResult | null>(null);

  // Validate URL as user types
  const urlValidation = useMemo(() => validateUrl(sourceUrl), [sourceUrl]);

  const mutation = useMutation({
    mutationFn: () => addFeed(sourceUrl, customSlug || undefined),
    onSuccess: (feed) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    },
  });

  // OPML import mutation
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
    if (sourceUrl.trim() && urlValidation.isValid) {
      mutation.mutate();
    }
  };

  return (
    <div className="max-w-xl mx-auto">
      <h1 className="text-2xl font-bold text-foreground mb-6">Add New Feed</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label htmlFor="sourceUrl" className="block text-sm font-medium text-foreground mb-2">
            Podcast RSS Feed URL
          </label>
          <input
            type="url"
            id="sourceUrl"
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            onBlur={() => setTouched(true)}
            placeholder="https://example.com/podcast/feed.xml"
            required
            className={`w-full px-4 py-2 rounded-lg border bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring ${
              touched && urlValidation.error
                ? 'border-destructive focus:ring-destructive'
                : touched && urlValidation.warning
                  ? 'border-yellow-500 focus:ring-yellow-500'
                  : 'border-input'
            }`}
          />
          {touched && urlValidation.error && (
            <p className="mt-1 text-sm text-destructive">
              {urlValidation.error}
            </p>
          )}
          {touched && !urlValidation.error && urlValidation.warning && (
            <p className="mt-1 text-sm text-yellow-600 dark:text-yellow-500">
              {urlValidation.warning}
            </p>
          )}
          {(!touched || (!urlValidation.error && !urlValidation.warning)) && (
            <p className="mt-1 text-sm text-muted-foreground">
              Enter the URL of the podcast RSS feed you want to add
            </p>
          )}
        </div>

        <div>
          <button
            type="button"
            onClick={() => setShowSlug(!showSlug)}
            className="text-sm text-primary hover:underline"
          >
            {showSlug ? 'Hide advanced options' : 'Show advanced options'}
          </button>

          {showSlug && (
            <div className="mt-4">
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
          )}
        </div>

        {mutation.error && (
          <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
            {(mutation.error as Error).message}
          </div>
        )}

        <div className="flex gap-4">
          <button
            type="submit"
            disabled={mutation.isPending || !sourceUrl.trim() || (touched && !urlValidation.isValid)}
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
      </form>

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
              <div className="animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full mx-auto"></div>
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

      {/* Podcast Index Link */}
      <div className="mt-8 pt-6 border-t border-border">
        <a
          href="https://podcastindex.org/"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <span>Search for podcasts on Podcast Index</span>
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>
      </div>
    </div>
  );
}

export default AddFeed;
