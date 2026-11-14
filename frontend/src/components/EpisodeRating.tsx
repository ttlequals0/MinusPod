import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Star } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { clearEpisodeRating, rateEpisode } from '../api/feeds';
import { getErrorMessage } from '../api/client';
import type { EpisodeDetail, EpisodeRating as Rating } from '../api/types';
import { btnBase, btnPrimary, btnSecondary, touchTarget } from './buttonStyles';
import { focusRing, textareaBase } from './fieldStyles';
import { cardTitle } from './pageStyles';
import { DemoDisabled } from './DemoDisabled';
import { IS_DEMO } from '../demo/flag';

const STARS = [1, 2, 3, 4, 5] as const;
const segments = (n: number, kind: string) => `${n} ${kind} ${n === 1 ? 'segment' : 'segments'}`;
const NOTES_PLACEHOLDER = 'What was wrong? Missed ad, wrong boundary, kept segment, audio glitch...';

const validRequest = (n?: number): n is number => Number.isInteger(n) && n! >= 1 && n! <= 5;
const stars = (n: number) => `${n} ${n === 1 ? 'star' : 'stars'}`;

interface EpisodeRatingProps {
  slug: string;
  episodeId: string;
  rating?: Rating | null;
  /** 1..5 from a feed link (?rate=N); applied once on mount. */
  requestedRating?: number;
  onRequestedRatingHandled?: () => void;
}

function EpisodeRating({ slug, episodeId, rating, requestedRating, onRequestedRatingHandled }: EpisodeRatingProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const requestHandled = useRef(false);
  const scrollToMessage = useRef(false);
  // What the episode had before a feed link rated it, so Undo puts it back.
  const priorRating = useRef<Rating | null>(null);
  const queryClient = useQueryClient();
  const [value, setValue] = useState<number>(rating?.value ?? 0);
  const [notes, setNotes] = useState(rating?.notes ?? '');
  // A link asking for the rating already stored needs no request, only the notice.
  const [message, setMessage] = useState<string | null>(() =>
    validRequest(requestedRating) && requestedRating === rating?.value ? `Already rated ${stars(requestedRating)}` : null);
  const [error, setError] = useState<string | null>(null);
  const [syncedAt, setSyncedAt] = useState(rating?.ratedAt ?? null);

  // Adopt a rating changed elsewhere (refetch) without clobbering an in-progress edit.
  if ((rating?.ratedAt ?? null) !== syncedAt) {
    setSyncedAt(rating?.ratedAt ?? null);
    setValue(rating?.value ?? 0);
    setNotes(rating?.notes ?? '');
  }

  const setRating = (next: Rating | null) => {
    queryClient.setQueryData<EpisodeDetail>(['episode', slug, episodeId], (old) => old && { ...old, rating: next });
    void queryClient.invalidateQueries({ queryKey: ['feed', slug], refetchType: 'none' });
  };

  const save = useMutation({
    mutationFn: ({ count, restoreNotes }: { count: number; viaLink?: boolean; restoreNotes?: string | null }) =>
      rateEpisode(slug, episodeId, count, count < 5 ? (restoreNotes ?? (notes.trim() || null)) : null),
    onMutate: () => { setError(null); setMessage(null); },
    onSuccess: (result, { count, viaLink, restoreNotes }) => {
      setValue(count);
      if (count === 5) setNotes('');
      if (restoreNotes !== undefined) {
        setNotes(restoreNotes ?? '');
        setMessage(`Restored ${stars(count)}`);
      } else if (viaLink) {
        setMessage(`Rated ${stars(count)} from the feed link`);
      } else {
        const { confirmedMarkers: confirmed, dismissedMarkers: dismissed } = result;
        const parts = [
          ...(confirmed || !dismissed ? [`${segments(confirmed, 'cut')} confirmed`] : []),
          ...(dismissed ? [`${segments(dismissed, 'held')} dismissed`] : []),
        ];
        setMessage(count === 5 ? `Saved. ${parts.join(', ')}.` : 'Rating saved.');
      }
      setRating(result.rating);
    },
    onError: (e) => setError(getErrorMessage(e, 'Could not save the rating.')),
  });

  const clear = useMutation({
    mutationFn: () => clearEpisodeRating(slug, episodeId),
    onMutate: () => { setError(null); setMessage(null); },
    onSuccess: () => {
      setValue(0);
      setNotes('');
      setMessage('Rating cleared.');
      setRating(null);
    },
    onError: (e) => setError(getErrorMessage(e, 'Could not clear the rating.')),
  });

  useEffect(() => {
    if (requestHandled.current || !validRequest(requestedRating)) return;
    requestHandled.current = true;
    if (!IS_DEMO && requestedRating !== rating?.value) {
      priorRating.current = rating ?? null;
      save.mutate({ count: requestedRating, viaLink: true });
    }
    scrollToMessage.current = true;
    onRequestedRatingHandled?.();
  }, [requestedRating, rating, save, onRequestedRatingHandled]);

  // The confirmation renders below the stars, so scroll once it exists.
  useEffect(() => {
    if (!scrollToMessage.current || !message) return;
    scrollToMessage.current = false;
    rootRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [message]);

  const undoable = save.isSuccess && !!save.variables?.viaLink && !clear.isSuccess && !clear.isPending;
  const undo = () => {
    const prior = priorRating.current;
    if (prior?.value) save.mutate({ count: prior.value, restoreNotes: prior.notes ?? null });
    else clear.mutate();
  };
  const busy = save.isPending || clear.isPending;
  const dirty = value !== (rating?.value ?? 0) || (value < 5 && notes.trim() !== (rating?.notes ?? ''));

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = Math.min(5, Math.max(1, (value || (step > 0 ? 0 : 6)) + step));
    setValue(next);
    (e.currentTarget.parentElement?.children[next - 1] as HTMLElement | undefined)?.focus();
  };

  return (
    <div ref={rootRef} className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id="episode-rating-title" className={cardTitle}>Rate this episode</h2>
        <p className="text-sm text-muted-foreground">
          {rating ? `Current rating: ${rating.value} of 5` : 'Not rated yet'}
        </p>
      </div>
      <p className="text-sm text-muted-foreground">
        5 stars confirms every cut segment in this episode and dismisses any held ones. Anything lower is kept as feedback.
      </p>
      <div role="radiogroup" aria-labelledby="episode-rating-title" className="flex items-center gap-1">
        {STARS.map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            aria-label={`${n} ${n === 1 ? 'star' : 'stars'}`}
            tabIndex={value === n || (value === 0 && n === 1) ? 0 : -1}
            disabled={busy}
            onClick={() => setValue(n)}
            onKeyDown={onKeyDown}
            className={`${touchTarget} sm:min-h-9 sm:pointer-fine:min-w-9 rounded text-warning ${focusRing}`}
          >
            <Star className="w-7 h-7" fill={n <= value ? 'currentColor' : 'none'} aria-hidden="true" />
          </button>
        ))}
      </div>
      {value > 0 && value < 5 && (
        <div>
          <label htmlFor="episode-rating-notes" className="sr-only">Rating notes</label>
          <textarea
            id="episode-rating-notes"
            rows={3}
            className={`${textareaBase} w-full`}
            placeholder={NOTES_PLACEHOLDER}
            value={notes}
            maxLength={2000}
            disabled={busy}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <DemoDisabled>
          <button
            type="button"
            className={`${btnBase} ${btnPrimary}`}
            disabled={value === 0 || !dirty || busy}
            onClick={() => save.mutate({ count: value })}
          >
            {save.isPending ? 'Saving...' : 'Save'}
          </button>
        </DemoDisabled>
        {rating && (
          <DemoDisabled>
            <button type="button" className={`${btnBase} ${btnSecondary}`} disabled={busy} onClick={() => clear.mutate()}>
              {clear.isPending ? 'Clearing...' : 'Clear'}
            </button>
          </DemoDisabled>
        )}
      </div>
      <div aria-live="polite" className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {message && <p className="text-sm text-success">{message}</p>}
        {undoable && (
          <DemoDisabled>
            <button type="button" className={`${btnBase} ${btnSecondary}`} disabled={busy} onClick={undo}>
              Undo
            </button>
          </DemoDisabled>
        )}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

export default EpisodeRating;
