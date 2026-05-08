import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTheme } from '../context/ThemeContext';
import { getSettings, updateSettings } from '../api/settings';
import CopyButton from './CopyButton';

/**
 * Renders the unified `/all` feed at the top of the Feeds list.
 *
 * Surface:
 * - MinusPod logo as artwork (matches the channel <itunes:image> served at /all)
 * - Subscribe URL with Copy button (uses the shared CopyButton, which handles
 *   the navigator.clipboard insecure-context fallback)
 * - Episode-limit input that auto-saves on blur via PUT /api/v1/settings
 */
function CombinedFeedCard() {
  const queryClient = useQueryClient();
  const { theme } = useTheme();

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const serverLimit = settings?.combinedFeedEpisodeLimit?.value ?? 50;
  const [limit, setLimit] = useState<number>(serverLimit);
  const [error, setError] = useState<string | null>(null);

  // Re-sync local state when the server value changes (e.g. another tab saved).
  useEffect(() => {
    setLimit(serverLimit);
  }, [serverLimit]);

  const combinedFeedUrl =
    typeof window !== 'undefined' ? `${window.location.origin}/all` : '/all';
  const logoSrc = theme === 'dark' ? '/ui/logo-dark.svg' : '/ui/logo.svg';

  const saveMutation = useMutation({
    mutationFn: (n: number) => updateSettings({ combinedFeedEpisodeLimit: n }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : 'Save failed');
      setLimit(serverLimit);
    },
  });

  const commitLimit = () => {
    const clamped = Math.max(1, Math.min(500, Number.isFinite(limit) ? limit : 50));
    if (clamped !== limit) setLimit(clamped);
    if (clamped !== serverLimit) {
      saveMutation.mutate(clamped);
    }
  };

  return (
    <div className="bg-card rounded-lg border border-border overflow-hidden mb-4">
      <div className="flex">
        <div className="w-24 h-24 shrink-0 bg-secondary/40 flex items-center justify-center p-3">
          <img
            src={logoSrc}
            alt="MinusPod"
            className="w-full h-full object-contain"
          />
        </div>
        <div className="flex-1 p-4 min-w-0">
          <div className="flex items-baseline justify-between gap-2 flex-wrap">
            <h3 className="text-lg font-semibold text-foreground truncate">
              All Podcasts (combined feed)
            </h3>
            <span className="text-xs text-muted-foreground uppercase tracking-wide">
              Unified RSS
            </span>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Subscribe to <code>/all</code> in your podcast app to receive the most-recent
            processed episodes from every show in MinusPod, newest first.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <label
              htmlFor="combinedFeedEpisodeLimit"
              className="text-sm text-muted-foreground"
            >
              Episodes:
            </label>
            <input
              type="number"
              id="combinedFeedEpisodeLimit"
              value={limit}
              onChange={(e) => setLimit(parseInt(e.target.value, 10) || 0)}
              onBlur={commitLimit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.currentTarget.blur();
                }
              }}
              min={1}
              max={500}
              className="w-20 px-2 py-1 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-sm"
              disabled={saveMutation.isPending}
            />
            <span className="text-xs text-muted-foreground">(1-500)</span>
            {saveMutation.isPending && (
              <span className="text-xs text-muted-foreground">Saving…</span>
            )}
            {error && (
              <span className="text-xs text-destructive" role="alert">{error}</span>
            )}
          </div>
        </div>
      </div>
      <div className="px-4 py-3 bg-secondary/50 border-t border-border flex items-center gap-2">
        <input
          type="text"
          readOnly
          value={combinedFeedUrl}
          onFocus={(e) => e.currentTarget.select()}
          className="flex-1 px-3 py-1.5 rounded-lg border border-input bg-background text-foreground text-sm font-mono focus:outline-hidden focus:ring-2 focus:ring-ring"
        />
        <CopyButton
          text={combinedFeedUrl}
          label="Copy Feed URL"
          className="px-3 py-1.5 border border-input bg-background hover:bg-muted text-foreground text-xs"
          labelClassName="text-xs"
        />
      </div>
    </div>
  );
}

export default CombinedFeedCard;
