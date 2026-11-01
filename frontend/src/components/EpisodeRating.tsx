import { useState, type KeyboardEvent } from 'react';
import { Star } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { clearEpisodeRating, rateEpisode } from '../api/feeds';
import { getErrorMessage } from '../api/client';
import type { EpisodeDetail, EpisodeRating as Rating } from '../api/types';
import { btnBase, btnPrimary, btnSecondary, touchTarget } from './buttonStyles';
import { focusRing, textareaBase } from './fieldStyles';
import { cardTitle } from './pageStyles';

const STARS = [1, 2, 3, 4, 5] as const;
const NOTES_PLACEHOLDER = 'What was wrong? Missed ad, wrong boundary, kept segment, audio glitch...';

function EpisodeRating({ slug, episodeId, rating }: { slug: string; episodeId: string; rating?: Rating | null }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState<number>(rating?.value ?? 0);
  const [notes, setNotes] = useState(rating?.notes ?? '');
  const [message, setMessage] = useState<string | null>(null);
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
    mutationFn: () => rateEpisode(slug, episodeId, value, value < 5 ? notes.trim() || null : null),
    onMutate: () => { setError(null); setMessage(null); },
    onSuccess: (result) => {
      if (value === 5) setNotes('');
      setMessage(value === 5
        ? `Saved. ${result.confirmedMarkers} cut ${result.confirmedMarkers === 1 ? 'segment' : 'segments'} confirmed.`
        : 'Rating saved.');
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
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id="episode-rating-title" className={cardTitle}>Rate this episode</h2>
        <p className="text-sm text-muted-foreground">
          {rating ? `Current rating: ${rating.value} of 5` : 'Not rated yet'}
        </p>
      </div>
      <p className="text-sm text-muted-foreground">
        5 stars confirms every cut segment in this episode. Anything lower is kept as feedback.
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
            className={`${touchTarget} sm:min-h-9 sm:min-w-9 rounded text-warning ${focusRing}`}
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
        <button
          type="button"
          className={`${btnBase} ${btnPrimary}`}
          disabled={value === 0 || !dirty || busy}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving...' : 'Save'}
        </button>
        {rating && (
          <button type="button" className={`${btnBase} ${btnSecondary}`} disabled={busy} onClick={() => clear.mutate()}>
            {clear.isPending ? 'Clearing...' : 'Clear'}
          </button>
        )}
      </div>
      <div aria-live="polite">
        {message && <p className="text-sm text-success">{message}</p>}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

export default EpisodeRating;
