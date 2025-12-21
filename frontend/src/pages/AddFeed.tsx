import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { addFeed } from '../api/feeds';

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

  // Validate URL as user types
  const urlValidation = useMemo(() => validateUrl(sourceUrl), [sourceUrl]);

  const mutation = useMutation({
    mutationFn: () => addFeed(sourceUrl, customSlug || undefined),
    onSuccess: (feed) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    },
  });

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
    </div>
  );
}

export default AddFeed;
