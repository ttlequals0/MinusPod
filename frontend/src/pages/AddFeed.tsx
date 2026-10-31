import { useState, useMemo, useRef, useCallback, useEffect } from 'react';
import { useNavigate, Link } from 'react-router';
import { Check, Mic, Plus, Search as SearchIcon, UploadCloud } from 'lucide-react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { getErrorMessage } from '../api/client';
import { addFeed, addYoutubeFeed, addLocalFeed, addRecentsFeed, uploadFeedArtwork, importOpml, OpmlImportResult, feedsQueryOptions } from '../api/feeds';
import { searchPodcasts, PodcastSearchResult } from '../api/podcastSearch';
import { previewYouTube, YOUTUBE_KIND_LABELS, YouTubePreview } from '../api/youtube';
import { getSettings } from '../api/settings';
import LoadingSpinner from '../components/LoadingSpinner';
import TriStateSelect from '../components/TriStateSelect';
import Checkbox from '../components/Checkbox';
import Artwork from '../components/Artwork';
import ExpandableText from '../components/ExpandableText';
import QueryError from '../components/QueryError';
import { badgeBase, tint } from '../components/badgeStyles';
import DraftNumberInput, { parseOptionalNumber } from '../components/DraftNumberInput';
import { btnBase, btnLg, btnPrimary, btnSecondary, btnGhost, touchTarget } from '../components/buttonStyles';
import { focusRing, inputBase, textareaBase, labelBase, fileInputBase } from '../components/fieldStyles';
import { pageHeader, pageTitle, cardTitle, subHeading } from '../components/pageStyles';

type AddFeedMode = 'subscribe' | 'local' | 'recents';

// Mirrors the shape of the backend's make_slug (python-slugify): lowercase,
// strip diacritics, collapse runs of non-alphanumerics to a single hyphen,
// trim leading/trailing hyphens, cap at the server's 200-char slug limit.
// The server re-validates and is the source of truth; this only keeps the
// preview from surprising the user before they submit.
function localSlugify(title: string): string {
  return title
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 200);
}

interface LocalFeedFormProps {
  onCancel: () => void;
}

// One combined feed of everything processed from now on (#721); title and
// description only, the rest is derived from the source feeds.
function RecentsFeedForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('Recents');
  const [description, setDescription] = useState('');
  const mutation = useMutation({
    mutationFn: () => addRecentsFeed({
      title: title.trim() || undefined,
      description: description.trim() || undefined,
    }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${result.slug}`);
    },
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); mutation.mutate(); }} className="space-y-4 max-w-xl">
      <p className="text-sm text-muted-foreground">
        One feed for newly processed episodes across your podcasts. Episodes published before today stay out.
      </p>
      <div>
        <label htmlFor="recentsTitle" className={labelBase}>Title</label>
        <input id="recentsTitle" type="text" value={title} onChange={(e) => setTitle(e.target.value)}
          className={`w-full ${inputBase}`} />
      </div>
      <div>
        <label htmlFor="recentsDescription" className={labelBase}>Description</label>
        <textarea id="recentsDescription" rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
          className={`w-full ${textareaBase}`} />
      </div>
      {mutation.error && (
        <p className="text-sm text-destructive">{getErrorMessage(mutation.error)}</p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={mutation.isPending}
          className={`${btnLg} ${btnPrimary}`}>
          {mutation.isPending ? 'Creating feed...' : 'Create feed'}
        </button>
        <button type="button" onClick={onCancel}
          className={`${btnLg} ${btnSecondary}`}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function LocalFeedForm({ onCancel }: LocalFeedFormProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [title, setTitle] = useState('');
  const [slug, setSlug] = useState('');
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState('');
  const [author, setAuthor] = useState('');
  const [explicit, setExplicit] = useState(false);
  const [categoriesInput, setCategoriesInput] = useState('');
  const [artworkFile, setArtworkFile] = useState<File | null>(null);
  // Set inside mutationFn when the create succeeds but the artwork upload
  // (a separate, non-fatal step) failed or came back with a warning; read by
  // onSuccess to attach a notice to the navigation, never to the create's
  // own error path.
  const artworkNoticeRef = useRef<string | null>(null);

  const handleTitleChange = (value: string) => {
    setTitle(value);
    if (!slugTouched) setSlug(localSlugify(value));
  };

  const handleSlugChange = (value: string) => {
    setSlugTouched(true);
    setSlug(value.toLowerCase().replace(/[^a-z0-9-]/g, ''));
  };

  const mutation = useMutation({
    mutationFn: async () => {
      artworkNoticeRef.current = null;
      const categories = categoriesInput
        .split(',')
        .map((c) => c.trim())
        .filter(Boolean);
      // slug is sent only when the user edited it by hand; otherwise the
      // server derives it from the title so the canonical slugify rules
      // (not this preview) decide the final value.
      const feed = await addLocalFeed({
        title: title.trim(),
        slug: slugTouched ? (slug || undefined) : undefined,
        description: description.trim() || undefined,
        author: author.trim() || undefined,
        explicit,
        categories: categories.length ? categories : undefined,
      });
      // A failed or flagged artwork upload never fails feed creation: the
      // feed already exists, so the mutation must still resolve and the
      // caller must still navigate there.
      if (artworkFile) {
        try {
          const result = await uploadFeedArtwork(feed.slug, artworkFile);
          if (result.warning) {
            artworkNoticeRef.current = `Feed created. ${result.warning}`;
          }
        } catch {
          artworkNoticeRef.current = 'Feed created. Could not upload artwork. Retry from the feed page.';
        }
      }
      return feed;
    },
    onSuccess: (feed) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      const notice = artworkNoticeRef.current;
      navigate(`/feeds/${feed.slug}`, notice ? { state: { notice } } : undefined);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (title.trim()) mutation.mutate();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label htmlFor="localTitle" className={labelBase}>
          Title
        </label>
        <input
          type="text"
          id="localTitle"
          value={title}
          onChange={(e) => handleTitleChange(e.target.value)}
          placeholder="My Archive Show"
          required
          className={`${inputBase} w-full`}
        />
      </div>

      <div>
        <label htmlFor="localSlug" className={labelBase}>
          Slug
        </label>
        <input
          type="text"
          id="localSlug"
          aria-describedby="localSlug-help"
          value={slug}
          onChange={(e) => handleSlugChange(e.target.value)}
          placeholder="my-archive-show"
          className={`${inputBase} w-full`}
        />
        <p id="localSlug-help" className="mt-1 text-sm text-muted-foreground">
          Cannot be changed after creation.
        </p>
      </div>

      <div>
        <label htmlFor="localDescription" className={labelBase}>
          Description
        </label>
        <textarea
          id="localDescription"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          className={`${textareaBase} w-full`}
        />
      </div>

      <div>
        <label htmlFor="localAuthor" className={labelBase}>
          Author
        </label>
        <input
          type="text"
          id="localAuthor"
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          className={`${inputBase} w-full`}
        />
      </div>

      {/* No id: Checkbox's id prop is only for when an ancestor already
          labels the box (renders a <span> then, no click-forwarding). No
          such ancestor here, so id would make the box visually present but
          unclickable by mouse -- the same bug LocalFeedPanel.tsx's identical
          checkbox had (#625 Task 13 review). */}
      <Checkbox
        checked={explicit}
        onChange={setExplicit}
        label="Explicit content"
      />

      <div>
        <label htmlFor="localCategories" className={labelBase}>
          Categories
        </label>
        <input
          type="text"
          id="localCategories"
          aria-describedby="localCategories-help"
          value={categoriesInput}
          onChange={(e) => setCategoriesInput(e.target.value)}
          placeholder="Technology, Business"
          className={`${inputBase} w-full`}
        />
        <p id="localCategories-help" className="mt-1 text-sm text-muted-foreground">
          Comma-separated, e.g. Technology, Business
        </p>
      </div>

      <div>
        <label htmlFor="localArtwork" className={labelBase}>
          Artwork
        </label>
        <input
          type="file"
          id="localArtwork"
          aria-describedby="localArtwork-help"
          accept="image/jpeg,image/png"
          onChange={(e) => setArtworkFile(e.target.files?.[0] ?? null)}
          className={fileInputBase}
        />
        <p id="localArtwork-help" className="mt-1 text-sm text-warning">
          Podcast apps require square artwork, at least 1400x1400.
        </p>
      </div>

      {mutation.error && (
        <div id="add-feed-error" role="alert" className="p-4 rounded-lg bg-destructive/10 text-destructive">
          {(mutation.error as Error).message}
        </div>
      )}

      <div className="flex gap-4">
        <button
          type="submit"
          disabled={mutation.isPending || !title.trim()}
          className={`${btnLg} ${btnPrimary} flex-1`}
        >
          {mutation.isPending ? 'Creating feed...' : 'Create feed'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className={`${btnLg} ${btnSecondary}`}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// URL validation patterns
const URL_PATTERN = /^https?:\/\/[a-zA-Z0-9][-a-zA-Z0-9]*(\.[a-zA-Z0-9][-a-zA-Z0-9]*)+.*$/i;
const YOUTUBE_HOSTS = ['youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'];
const YOUTUBE_TOKEN = /^((PL|UU|UULF|UUSH|UULV)[A-Za-z0-9_-]{10,}|UC[A-Za-z0-9_-]{22}|@[A-Za-z0-9._-]{2,64})$/;

const hasProtocol = (v: string) => /^https?:\/\//i.test(v);

// Mirrors youtube.urls.parse acceptance.
function isYouTubeInput(text: string): boolean {
  const value = text.trim();
  if (hasProtocol(value)) {
    try {
      return YOUTUBE_HOSTS.includes(new URL(value).hostname.toLowerCase());
    } catch {
      return false;
    }
  }
  return YOUTUBE_TOKEN.test(value);
}

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
    if (!hasProtocol(url)) {
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
  const isHttps = /^https:\/\//i.test(url);

  // Check if it looks like an RSS feed
  const looksLikeRss = RSS_EXTENSIONS.some(ext =>
    url.toLowerCase().includes(ext)
  );

  return {
    isValid: true,
    error: null,
    warning: !looksLikeRss && isHttps
      ? 'This may not be a podcast RSS feed.'
      : !isHttps
        ? 'Use HTTPS for a secure connection.'
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
          className="w-14 h-14 rounded object-cover shrink-0 bg-muted"
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setImageError(true)}
        />
      ) : (
        <div className="w-14 h-14 rounded bg-muted shrink-0 flex items-center justify-center">
          <Mic className="w-6 h-6 text-muted-foreground" aria-hidden="true" />
        </div>
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {result.id > 0 ? (
              <a
                href={`https://podcastindex.org/podcast/${result.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className={`text-sm font-semibold text-foreground hover:text-primary truncate block ${focusRing}`}
                title={result.title}
              >
                {result.title}
              </a>
            ) : (
              <span className="text-sm font-semibold text-foreground truncate block" title={result.title}>
                {result.title}
              </span>
            )}
            {result.author && (
              <p className="text-xs text-muted-foreground truncate">{result.author}</p>
            )}
          </div>
          {isSubscribed ? (
            <span className="shrink-0 text-muted-foreground" title="Already subscribed">
              <Check className="w-5 h-5" aria-hidden="true" />
            </span>
          ) : (
            <button
              onClick={handleAdd}
              disabled={isAdding}
              className={`${touchTarget} ${btnGhost} shrink-0 rounded text-primary disabled:opacity-50 ${focusRing}`}
              title="Add this podcast"
            >
              {isAdding ? (
                <LoadingSpinner size="sm" inline />
              ) : (
                <Plus className="w-5 h-5" aria-hidden="true" />
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

const MODES: [AddFeedMode, string][] = [
  ['subscribe', 'Subscribe to feed'],
  ['local', 'Create local feed'],
  ['recents', 'Create recents feed'],
];

function AddFeed() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<AddFeedMode>('subscribe');

  // Input state
  const [inputValue, setInputValue] = useState('');
  const [customSlug, setCustomSlug] = useState('');
  const [autoProcessOverride, setAutoProcessOverride] = useState<boolean | null>(null);
  const [maxEpisodes, setMaxEpisodes] = useState<string>('');
  const [onlyExposeProcessedEpisodes, setOnlyExposeProcessedEpisodes] = useState<boolean | null>(null);

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
  const isUrl = hasProtocol(inputValue.trim());

  // URL validation (only when it looks like a URL)
  const isYouTube = isYouTubeInput(inputValue);
  const urlValidation = useMemo(() => {
    if (!isUrl) return { isValid: false, error: null, warning: null };
    const result = validateUrl(inputValue.trim());
    return isYouTube ? { ...result, warning: null } : result;
  }, [inputValue, isUrl, isYouTube]);
  const [touched, setTouched] = useState(false);

  // Search is gated on any resolved provider; iTunes needs no keys.
  const { data: settings, isPending: settingsPending, isError: settingsError, isFetching: settingsFetching, refetch: refetchSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });
  const searchAvailable = settings?.podcastSearchReady ?? false;
  const youtubeEnabled = settings?.youtubeEnabled?.value ?? false;
  const showYoutubeDisabled = isYouTube && !!settings && !youtubeEnabled;

  // Existing feeds for "already added" detection
  const { data: feedsData } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const hasRecents = (feedsData ?? []).some((f) => f.feedType === 'recents');
  const subscribedUrls = useMemo(() => {
    if (!feedsData) return new Set<string>();
    return new Set(feedsData.map((f) => f.sourceUrl));
  }, [feedsData]);

  const inputTrimmed = inputValue.trim();
  const shouldSearch = !isUrl && !isYouTube && searchAvailable && inputTrimmed.length >= 2;

  // Clear stale search state during render when the search is no longer
  // applicable. Avoids a setState-in-effect for the early-return branch.
  if (!shouldSearch && (searchResults.length > 0 || searchError !== null)) {
    setSearchResults([]);
    setSearchError(null);
  }

  // Debounced search with AbortController to cancel stale requests
  useEffect(() => {
    if (!shouldSearch) return;

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setIsSearching(true);
      setSearchError(null);
      try {
        const results = await searchPodcasts(inputTrimmed, controller.signal);
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
  }, [inputTrimmed, shouldSearch]);

  // Add feed mutation (for URL submit)
  const mutation = useMutation({
    mutationFn: () => addFeed(inputValue, customSlug || undefined, autoProcessOverride, maxEpisodes ? parseInt(maxEpisodes, 10) : undefined, onlyExposeProcessedEpisodes ?? undefined),
    onSuccess: (feed) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    },
  });

  // YouTube lookup: preview a channel, playlist or video before adding it
  const [youtubePreview, setYoutubePreview] = useState<YouTubePreview | null>(null);
  const previewAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => previewAbortRef.current?.abort(), []);
  const previewMutation = useMutation({
    mutationFn: async (url: string) => {
      previewAbortRef.current?.abort();
      const controller = new AbortController();
      previewAbortRef.current = controller;
      const data = await previewYouTube(url, controller.signal);
      return controller.signal.aborted ? null : data;
    },
    onSuccess: (data) => { if (data) setYoutubePreview(data); },
  });
  const addYoutubeMutation = useMutation({
    mutationFn: (source: NonNullable<YouTubePreview['source']>) => addYoutubeFeed({
      sourceKind: source.kind,
      sourceId: source.id,
      title: source.title,
      description: source.description,
      author: source.author,
      artworkUrl: source.artworkUrl,
      includeShorts: false,
      slug: customSlug || undefined,
      autoProcessOverride,
      maxEpisodes: maxEpisodes ? Number(maxEpisodes) : undefined,
      onlyExposeProcessedEpisodes,
    }),
    onSuccess: (feed) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    },
  });
  const youtubeBusy = previewMutation.isPending || addYoutubeMutation.isPending;
  const youtubeBlocked = !youtubeEnabled || !!settingsError;
  const youtubeError = previewMutation.error ?? addYoutubeMutation.error;
  const youtubeSource = youtubePreview?.source ?? null;

  const handleInputChange = (value: string) => {
    setInputValue(value);
    mutation.reset();
    previewAbortRef.current?.abort();
    setYoutubePreview(null);
    previewMutation.reset();
    addYoutubeMutation.reset();
  };

  // Add feed from search result
  const addFromSearch = useCallback(async (feedUrl: string) => {
    setAddingFeedUrl(feedUrl);
    try {
      const feed = await addFeed(feedUrl, customSlug || undefined, autoProcessOverride, maxEpisodes ? parseInt(maxEpisodes, 10) : undefined, onlyExposeProcessedEpisodes ?? undefined);
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      navigate(`/feeds/${feed.slug}`);
    } finally {
      setAddingFeedUrl(null);
    }
  }, [customSlug, autoProcessOverride, maxEpisodes, onlyExposeProcessedEpisodes, queryClient, navigate]);

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
    if (isYouTube) {
      if (youtubeEnabled && !previewMutation.isPending) previewMutation.mutate(inputValue.trim());
    } else if (isUrl && inputValue.trim() && urlValidation.isValid) {
      mutation.mutate();
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <div className={pageHeader}>
        <h1 className={pageTitle}>Add feed</h1>
      </div>

      {/* Mode toggle: subscribed feeds pull from an upstream RSS URL; local
          feeds have no upstream source and are seeded entirely from this form. */}
      <div className="grid grid-cols-1 min-[560px]:flex border border-border rounded overflow-hidden mb-6 w-full min-[560px]:w-fit [&>button]:rounded-none">
        {MODES.filter(([value]) => value !== 'recents' || !hasRecents).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setMode(value)}
            aria-pressed={mode === value}
            className={`${btnLg} ${mode === value ? btnPrimary : btnSecondary}`}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === 'local' && <LocalFeedForm onCancel={() => setMode('subscribe')} />}
      {mode === 'recents' && <RecentsFeedForm onCancel={() => setMode('subscribe')} />}

      {mode === 'subscribe' && (
      <>
      {settingsPending && <p role="status" className="mb-4 text-sm text-muted-foreground">Loading feed options...</p>}
      {settingsError && <div className="mb-4"><QueryError title="Could not load feed options." onRetry={() => void refetchSettings()} retrying={settingsFetching} /></div>}
      {settings && !settings.podcastSearchReady && (
        <div className="mb-4 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
          PodcastIndex is selected but its credentials are missing. Add them in{' '}
          <Link to="/settings#podcast-index" className="underline">Settings</Link>
          {' '}or switch the provider to iTunes.
        </div>
      )}
      {/* Section A: Unified Input */}
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="podcastInput" className={labelBase}>
            {searchAvailable ? 'Search podcasts or enter an RSS or YouTube URL' : 'RSS or YouTube URL'}
          </label>
          <input
            type="text"
            id="podcastInput"
            aria-invalid={isUrl && touched && !!urlValidation.error}
            aria-describedby={[isUrl && touched && (urlValidation.error || urlValidation.warning) ? "podcast-url-feedback" : "", showYoutubeDisabled ? "youtube-disabled" : "", mutation.error || youtubeError ? "add-feed-error" : ""].filter(Boolean).join(" ") || undefined}
            value={inputValue}
            disabled={addYoutubeMutation.isPending}
            onChange={(e) => handleInputChange(e.target.value)}
            onBlur={() => { if (isUrl) setTouched(true); }}
            placeholder="Podcast name, RSS URL or YouTube URL"
            className={`${inputBase} w-full placeholder:text-muted-foreground ${
              isUrl && touched && urlValidation.error
                ? 'border-destructive focus-visible:ring-destructive'
                : isUrl && touched && urlValidation.warning
                  ? 'border-warning focus-visible:ring-warning'
                  : ''
            }`}
          />
          {isUrl && touched && urlValidation.error && (
            <p id="podcast-url-feedback" role="alert" className="mt-1 text-sm text-destructive">{urlValidation.error}</p>
          )}
          {isUrl && touched && !urlValidation.error && urlValidation.warning && (
            <p id="podcast-url-feedback" className="mt-1 text-sm text-warning">{urlValidation.warning}</p>
          )}
          {showYoutubeDisabled && (
            <p id="youtube-disabled" className="mt-1 text-sm text-warning">
              YouTube feeds are turned off. Enable them in{' '}
              <Link to="/settings#youtube" className="underline">Settings</Link>.
            </p>
          )}
        </div>

        {/* Section B: Advanced Settings (collapsible) */}
        <details className="group">
          <summary className="text-sm text-primary hover:underline cursor-pointer list-none">
            Advanced options
            <span className="text-muted-foreground font-normal"> (URL and search feeds)</span>
          </summary>
          <div className="mt-4 space-y-4">
            <div>
              <label htmlFor="slug" className={labelBase}>
                Custom slug (optional)
              </label>
              <input
                type="text"
                id="slug"
          aria-describedby="slug-help"
                value={customSlug}
                onChange={(e) => setCustomSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                placeholder="my-podcast"
                className={`${inputBase} w-full`}
              />
              <p id="slug-help" className="mt-1 text-sm text-muted-foreground">
                Custom URL path for this feed. Only lowercase letters, numbers, and hyphens.
              </p>
            </div>

            <div>
              <label htmlFor="autoProcess" className={labelBase}>
                Auto-process
              </label>
              <TriStateSelect
                id="autoProcess"
          ariaDescribedBy="autoProcess-help"
                value={autoProcessOverride}
                onChange={setAutoProcessOverride}
              />
              <p id="autoProcess-help" className="mt-1 text-sm text-muted-foreground">
                Automatically process new episodes.
              </p>
            </div>

            <div>
              <label htmlFor="maxEpisodes" className={labelBase}>
                Max episodes in feed
              </label>
              <DraftNumberInput
                id="maxEpisodes"
          ariaDescribedBy="maxEpisodes-help"
                value={parseOptionalNumber(maxEpisodes)}
                fallback={null}
                parse={parseOptionalNumber}
                onChange={(v) => setMaxEpisodes(v === null ? '' : String(v))}
                placeholder="300 (default)"
                min={10}
                max={500}
                step={1}
                className="w-full"
              />
              <p id="maxEpisodes-help" className="mt-1 text-sm text-muted-foreground">
                Limits how many episodes are served to podcast clients. Max: 500.
              </p>
            </div>

            <div>
              <label htmlFor="onlyExposeProcessedEpisodes" className={labelBase}>
                Only expose processed episodes in feed
              </label>
              <TriStateSelect
                id="onlyExposeProcessedEpisodes"
          ariaDescribedBy="onlyExposeProcessedEpisodes-help"
                value={onlyExposeProcessedEpisodes}
                onChange={setOnlyExposeProcessedEpisodes}
              />
              <p id="onlyExposeProcessedEpisodes-help" className="mt-1 text-sm text-muted-foreground">
                Hide upstream episodes until processing finishes. Global default uses the site-wide setting.
              </p>
            </div>
          </div>
        </details>

        {/* URL mode: show Add Feed button */}
        {(isUrl || isYouTube) && (
          <>
            {(isYouTube ? youtubeError : mutation.error) && (
              <div id="add-feed-error" role="alert" className="p-4 rounded-lg bg-destructive/10 text-destructive">
                {getErrorMessage(isYouTube ? youtubeError : mutation.error)}
              </div>
            )}
            <div className="flex gap-4">
              <button
                type="submit"
                disabled={isYouTube
                  ? youtubeBusy || youtubeBlocked
                  : mutation.isPending || !inputValue.trim() || (touched && !urlValidation.isValid)}
                className={`${btnLg} ${btnPrimary} flex-1`}
              >
                {isYouTube ? <SearchIcon className="w-4 h-4" aria-hidden="true" /> : <Plus className="w-4 h-4" aria-hidden="true" />}
                {isYouTube
                  ? (previewMutation.isPending ? 'Looking up...' : 'Look up')
                  : (mutation.isPending ? 'Adding feed...' : 'Add feed')}
              </button>
              <button
                type="button"
                onClick={() => navigate('/')}
                className={`${btnLg} ${btnSecondary}`}
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </form>

      {isYouTube && youtubeSource && (
        <div className="mt-4 space-y-4">
          <div className="border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-start gap-3">
              {youtubeSource.artworkUrl ? (
                <Artwork src={youtubeSource.artworkUrl} alt="" className="w-14 h-14 rounded object-cover shrink-0 bg-muted" loading="lazy" />
              ) : (
                <div className="w-14 h-14 rounded bg-muted shrink-0 flex items-center justify-center">
                  <Mic className="w-6 h-6 text-muted-foreground" aria-hidden="true" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <h2 className={`${cardTitle} break-words`}>{youtubeSource.title}</h2>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                  <span className={`${badgeBase} ${tint.primary}`}>
                    {YOUTUBE_KIND_LABELS[youtubeSource.kind]}
                  </span>
                  {youtubeSource.kind !== 'video' && youtubeSource.videoCount != null && <span>{youtubeSource.videoCount} videos</span>}
                </div>
              </div>
            </div>
            {youtubeSource.description && (
              <ExpandableText clampLines={3} label="description" className="text-sm text-muted-foreground">
                {youtubeSource.description}
              </ExpandableText>
            )}
            {youtubeSource.alreadyAdded ? (
              <Link to={`/feeds/${youtubeSource.alreadyAdded}`} className={`text-sm text-primary underline ${focusRing}`}>
                Already added, open feed
              </Link>
            ) : (
              <div className="grid grid-cols-1 min-[560px]:flex gap-2">
                <button
                  type="button"
                  onClick={() => addYoutubeMutation.mutate(youtubeSource)}
                  disabled={youtubeBusy || youtubeBlocked}
                  className={`${btnLg} ${btnPrimary}`}
                >
                  <Plus className="w-4 h-4" aria-hidden="true" />
                  {addYoutubeMutation.isPending ? 'Adding feed...' : 'Add YouTube feed'}
                </button>
              </div>
            )}
          </div>
          {youtubePreview && youtubePreview.rssMatches.length > 0 && (
            <div className="space-y-2">
              <h3 className={subHeading}>Or add by RSS feed</h3>
              {youtubePreview.rssMatches.map((match) => (
                <SearchResultItem
                  key={match.feedUrl}
                  result={{ id: 0, title: match.title, description: '', artworkUrl: match.artworkUrl, feedUrl: match.feedUrl, author: '', link: '' }}
                  isSubscribed={subscribedUrls.has(match.feedUrl)}
                  isAdding={addingFeedUrl === match.feedUrl}
                  onAdd={addFromSearch}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Section C: Search Results */}
      {!isUrl && !isYouTube && inputValue.trim() && searchAvailable && (
        <div className="mt-4 space-y-2">
          {isSearching && (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border animate-pulse">
                  <div className="w-14 h-14 rounded bg-muted shrink-0" />
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
            <p className="text-sm text-muted-foreground py-4 text-center">No results for "{inputValue.trim()}".</p>
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
        <h2 className={cardTitle}>Import from OPML</h2>
        <p className="text-sm text-muted-foreground">
          Upload an OPML file to import multiple feeds
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
              <UploadCloud className="w-12 h-12 mx-auto text-muted-foreground" aria-hidden="true" />
              <div>
                <p className="text-foreground">Drop your OPML file here, or</p>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className={`${btnBase} ${btnGhost} text-primary`}
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
            <h3 className={`${subHeading} mb-2`}>Import results</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="text-success">
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
                <p className="text-muted-foreground mb-1">Could not import these feeds:</p>
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
              className={`${btnBase} ${btnGhost} mt-3`}
            >
              Dismiss
            </button>
          </div>
        )}
      </div>
      </>
      )}
    </div>
  );
}

export default AddFeed;
