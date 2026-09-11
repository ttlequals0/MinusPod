import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  assignUnresolvedCorrection,
  getUnresolvedCorrections,
  type UnresolvedCorrection,
} from '../../api/patterns';
import { btnPrimary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';

function formatBounds(bounds: { start: number; end: number } | null): string | null {
  if (!bounds) return null;
  return `${bounds.start.toFixed(1)} s to ${bounds.end.toFixed(1)} s`;
}

function CorrectionRow({ correction }: { correction: UnresolvedCorrection }) {
  const queryClient = useQueryClient();
  const [slug, setSlug] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const mutation = useMutation({
    mutationFn: () => assignUnresolvedCorrection(correction.id, slug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['unresolved-corrections'] }),
  });

  return (
    <li className="rounded border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium text-foreground">Correction #{correction.id}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {correction.episode_title || correction.episode_id}
          </p>
          {correction.podcast_title && (
            <p className="text-xs text-muted-foreground">
              Saved under {correction.podcast_title}
            </p>
          )}
          {formatBounds(correction.original_bounds) && (
            <p className="text-xs text-muted-foreground">
              Original segment: {formatBounds(correction.original_bounds)}
            </p>
          )}
        </div>
        <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
          {correction.correction_type.replace(/_/g, ' ')}
        </span>
      </div>

      {correction.candidates.length > 0 ? (
        <fieldset className="mt-4 space-y-2">
          <legend className="text-sm font-medium text-foreground">Choose the feed</legend>
          {correction.candidates.map((candidate) => (
            <div
              key={candidate.slug}
              className={`flex cursor-pointer gap-3 rounded border p-3 ${
                slug === candidate.slug ? 'border-primary bg-primary/5' : 'border-border'
              }`}
            >
              <label className="flex min-w-0 flex-1 cursor-pointer gap-3">
                <input
                  type="radio"
                  name={`correction-${correction.id}`}
                  value={candidate.slug}
                  checked={slug === candidate.slug}
                  onChange={() => { setSlug(candidate.slug); setConfirmed(false); }}
                  className="mt-1"
                />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-foreground">
                    {candidate.podcast_title || candidate.slug}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {candidate.slug} / {candidate.episode_title || correction.episode_id}
                  </span>
                </span>
              </label>
              <a
                href={`/ui/feeds/${encodeURIComponent(candidate.slug)}/episodes/${encodeURIComponent(correction.episode_id)}`}
                className={`shrink-0 self-center text-xs text-primary hover:underline ${focusRing}`}
              >
                Review episode
              </a>
            </div>
          ))}
        </fieldset>
      ) : (
        <p className="mt-4 text-sm text-warning">No matching feed is available.</p>
      )}

      {slug && (
        <label className="mt-4 flex items-start gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            className="mt-1"
          />
          <span>I checked the episode and confirm this feed.</span>
        </label>
      )}

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          disabled={!slug || !confirmed || mutation.isPending}
          onClick={() => mutation.mutate()}
          className={`rounded px-3 py-2 text-sm ${btnPrimary} ${focusRing} disabled:opacity-50`}
        >
          {mutation.isPending ? 'Assigning...' : 'Assign correction'}
        </button>
        {mutation.error && (
          <p className="text-sm text-destructive">{(mutation.error as Error).message}</p>
        )}
      </div>
    </li>
  );
}

export default function UnresolvedCorrectionsPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['unresolved-corrections'],
    queryFn: getUnresolvedCorrections,
  });

  if (isLoading || (!error && !data?.count)) return null;

  return (
    <section className="mb-6 rounded-lg border border-warning/40 bg-warning/5 p-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold text-foreground">Unassigned corrections</h2>
        {data && (
          <span className="rounded-full bg-warning/20 px-2 py-0.5 text-xs font-medium text-warning">
            {data.count}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Choose the feed that owns each legacy correction. The assignment changes saved review history.
      </p>
      {error ? (
        <p className="mt-3 text-sm text-destructive">Could not load unassigned corrections.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {data?.corrections.map((correction) => (
            <CorrectionRow key={correction.id} correction={correction} />
          ))}
        </ul>
      )}
    </section>
  );
}
