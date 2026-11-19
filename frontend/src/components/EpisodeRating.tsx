import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { clearEpisodeRating, rateEpisode } from '../api/feeds';
import { getErrorMessage } from '../api/client';
import type { EpisodeDetail, EpisodeRating as Rating, RatingValue } from '../api/types';
import { btnBase, btnPrimary, btnSecondary, touchTarget } from './buttonStyles';
import { focusRing, textareaBase } from './fieldStyles';
import { cardTitle } from './pageStyles';
import Checkbox from './Checkbox';
import { DemoDisabled } from './DemoDisabled';
import { IS_DEMO } from '../demo/flag';

const CHOICES = [
  { value: 'up', label: 'Thumbs up', Icon: ThumbsUp },
  { value: 'down', label: 'Thumbs down', Icon: ThumbsDown },
] as const;
const segments = (n: number, kind: string) => `${n} ${kind} ${n === 1 ? 'segment' : 'segments'}`;
const NOTES_PLACEHOLDER = 'What was wrong? Missed ad, wrong boundary, kept segment, audio glitch...';

const thumbs = (v: RatingValue) => `thumbs ${v}`;

interface EpisodeRatingProps {
  slug: string;
  episodeId: string;
  rating?: Rating | null;
  /** From a feed link (?rate=); applied once on mount. */
  requestedRating?: RatingValue;
  onRequestedRatingHandled?: () => void;
}

function EpisodeRating({ slug, episodeId, rating, requestedRating, onRequestedRatingHandled }: EpisodeRatingProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const requestHandled = useRef(false);
  const scrollToMessage = useRef(false);
  // What the episode had before a feed link rated it, so Undo puts it back.
  const priorRating = useRef<Rating | null>(null);
  const notesRef = useRef<HTMLTextAreaElement>(null);
  const focusNotes = useRef(false);
  const queryClient = useQueryClient();
  const [value, setValue] = useState<RatingValue | null>(rating?.value ?? null);
  const [notes, setNotes] = useState(rating?.notes ?? '');
  const [confirmMarkers, setConfirmMarkers] = useState(true);
  // A link asking for the rating already stored needs no request, only the notice.
  const [message, setMessage] = useState<string | null>(() =>
    requestedRating && requestedRating === rating?.value ? `Already rated ${thumbs(requestedRating)}` : null);
  const [error, setError] = useState<string | null>(null);
  const [syncedAt, setSyncedAt] = useState(rating?.ratedAt ?? null);

  // Adopt a rating changed elsewhere (refetch) without clobbering an in-progress edit.
  if ((rating?.ratedAt ?? null) !== syncedAt) {
    setSyncedAt(rating?.ratedAt ?? null);
    setValue(rating?.value ?? null);
    setNotes(rating?.notes ?? '');
  }

  const setRating = (next: Rating | null) => {
    queryClient.setQueryData<EpisodeDetail>(['episode', slug, episodeId], (old) => old && { ...old, rating: next });
    void queryClient.invalidateQueries({ queryKey: ['feed', slug], refetchType: 'none' });
  };

  const save = useMutation({
    mutationFn: ({ choice, restoreNotes, confirm }: { choice: RatingValue; viaLink?: boolean; restoreNotes?: string | null; confirm?: false }) =>
      rateEpisode(slug, episodeId, choice, choice === 'down' ? (restoreNotes ?? (notes.trim() || null)) : null,
        ...(confirm === undefined ? [] : [confirm])),
    onMutate: () => { setError(null); setMessage(null); },
    onSuccess: (result, { choice, viaLink, restoreNotes, confirm }) => {
      setValue(choice);
      setConfirmMarkers(true);
      if (choice === 'up') setNotes('');
      if (restoreNotes !== undefined) {
        setNotes(restoreNotes ?? '');
        setMessage(`Restored ${thumbs(choice)}`);
      } else if (viaLink) {
        setMessage(`Rated ${thumbs(choice)} from the feed link`);
      } else {
        const { confirmedMarkers: confirmed, dismissedMarkers: dismissed } = result;
        const parts = [
          ...(confirmed || !dismissed ? [`${segments(confirmed, 'cut')} confirmed`] : []),
          ...(dismissed ? [`${segments(dismissed, 'held')} dismissed`] : []),
        ];
        setMessage(choice === 'up' && confirm !== false ? `Saved. ${parts.join(', ')}.` : 'Rating saved.');
      }
      setRating(result.rating);
    },
    onError: (e) => setError(getErrorMessage(e, 'Could not save the rating.')),
  });

  const clear = useMutation({
    mutationFn: () => clearEpisodeRating(slug, episodeId),
    onMutate: () => { setError(null); setMessage(null); },
    onSuccess: () => {
      setValue(null);
      setNotes('');
      setMessage('Rating cleared.');
      setRating(null);
    },
    onError: (e) => setError(getErrorMessage(e, 'Could not clear the rating.')),
  });

  useEffect(() => {
    if (requestHandled.current || !requestedRating) return;
    requestHandled.current = true;
    if (!IS_DEMO && requestedRating !== rating?.value) {
      priorRating.current = rating ?? null;
      save.mutate({ choice: requestedRating, viaLink: true });
    }
    focusNotes.current = requestedRating === 'down';
    scrollToMessage.current = true;
    onRequestedRatingHandled?.();
  }, [requestedRating, rating, save, onRequestedRatingHandled]);

  // A ?rate=down link lands the listener in the comment box once it is rendered.
  useEffect(() => {
    if (!focusNotes.current || value !== 'down' || !notesRef.current) return;
    focusNotes.current = false;
    notesRef.current.focus();
  }, [value]);

  // The confirmation renders below the buttons, so scroll once it exists.
  useEffect(() => {
    if (!scrollToMessage.current || !message) return;
    scrollToMessage.current = false;
    rootRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [message]);

  const undoable = save.isSuccess && !!save.variables?.viaLink && !clear.isSuccess && !clear.isPending;
  const undo = () => {
    const prior = priorRating.current;
    if (prior) save.mutate({ choice: prior.value, restoreNotes: prior.notes ?? null });
    else clear.mutate();
  };
  const busy = save.isPending || clear.isPending;
  const dirty = value !== (rating?.value ?? null) || (value === 'down' && notes.trim() !== (rating?.notes ?? ''));

  // Choosing a thumb re-arms the confirm option, so an earlier untick never carries over.
  const pick = (v: RatingValue) => { setValue(v); setConfirmMarkers(true); };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = value === null ? (step > 0 ? 0 : 1) : (step > 0 ? 1 : 0);
    pick(CHOICES[next].value);
    (e.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
  };

  return (
    <div ref={rootRef} className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id="episode-rating-title" className={cardTitle}>Rate this episode</h2>
        <p className="text-sm text-muted-foreground">
          {rating ? `Current rating: ${thumbs(rating.value)}` : 'Not rated yet'}
        </p>
      </div>
      <p className="text-sm text-muted-foreground">
        Thumbs up confirms every cut segment in this episode and dismisses any held ones. Thumbs down is kept as feedback, with an optional comment.
      </p>
      <div role="radiogroup" aria-labelledby="episode-rating-title" className="flex flex-wrap items-center gap-2">
        {CHOICES.map(({ value: v, label, Icon }) => {
          const selected = value === v;
          return (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected || (value === null && v === 'up') ? 0 : -1}
              disabled={busy}
              onClick={() => pick(v)}
              onKeyDown={onKeyDown}
              className={`${touchTarget} sm:min-h-9 sm:pointer-fine:min-w-9 gap-2 px-3 rounded border text-sm transition-colors disabled:opacity-50 ${focusRing} ${selected ? 'border-primary bg-accent font-medium text-accent-foreground' : 'border-input text-muted-foreground hover:bg-accent hover:text-foreground'}`}
            >
              <Icon className="w-5 h-5" fill={selected ? 'currentColor' : 'none'} aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>
      {value === 'up' && rating?.value !== 'up' && (
        <div>
          <Checkbox
            className="pointer-coarse:min-h-11"
            checked={confirmMarkers}
            onChange={setConfirmMarkers}
            disabled={busy}
            ariaDescribedBy="episode-rating-confirm-help"
            label="Confirm all cuts and dismiss held ones"
          />
          <p id="episode-rating-confirm-help" className="text-sm text-muted-foreground">Untick to store a thumbs up without confirming anything.</p>
        </div>
      )}
      {value === 'down' && (
        <div>
          <label htmlFor="episode-rating-notes" className="sr-only">Comment</label>
          <textarea
            ref={notesRef}
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
            disabled={!value || !dirty || busy}
            onClick={() => value && save.mutate({ choice: value, confirm: value === 'up' && !confirmMarkers ? false : undefined })}
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
